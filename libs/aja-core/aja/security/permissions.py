from dataclasses import dataclass, field
import fnmatch
import os
import re
import sys
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional
import uuid

from aja.security.command_guard import command_allowed


PermissionDecision = str


class PermissionError(Exception):
    pass


@dataclass
class AuthorizationResult:
    allowed: bool
    decision: PermissionDecision
    scope: str
    reason: str = ""
    grant_id: Optional[str] = None
    matched_scope: Optional[str] = None


@dataclass
class PermissionPolicy:
    scopes: Dict[str, PermissionDecision] = field(default_factory=dict)
    ask_timeout_s: float = 60.0

    @classmethod
    def defaults(cls) -> "PermissionPolicy":
        return cls(
            scopes={
                "shell.*": "allow",
                "shell.destructive": "ask",
                "shell.exec.dangerous": "ask",
                "python.*": "allow",
                "notify.*": "allow",
                "mobile.*": "allow",
                "mcp.*": "ask",
                # Web research tools (Phase 6): read-only network operations,
                # allowed by default so autonomous research missions work
                # unattended. fetch_url/search_url perform GETs only.
                "web.read": "allow",
                "web.search": "allow",
                "browser.read": "allow",
                "browser.navigate": "ask",
                "browser.interact": "ask",
                "desktop.interact": "ask",
                "fs.read.global": "allow",
                "fs.write.global": "ask",
                "fs.read.sensitive": "ask",
                "fs.write.sensitive": "ask",
            },
            ask_timeout_s=60.0,
        )

    @classmethod
    def from_config(cls, config: Any) -> "PermissionPolicy":
        if config is None:
            return cls.defaults()
        scopes = dict(getattr(config, "scopes", {}) or {})
        ask_timeout_s = float(getattr(config, "ask_timeout_s", 60.0))
        if not scopes:
            scopes = cls.defaults().scopes
        return cls(scopes=scopes, ask_timeout_s=ask_timeout_s)

    def decision_for(self, scope: str) -> tuple[PermissionDecision, Optional[str]]:
        matches = []
        for pattern, decision in self.scopes.items():
            if _scope_matches(pattern, scope):
                matches.append((pattern, decision))
        if not matches:
            return "deny", None

        # Deny wins even when a broader allow also matches.
        for pattern, decision in matches:
            if decision == "deny":
                return decision, pattern

        # Prefer the most specific remaining rule.
        matches.sort(key=lambda item: _specificity(item[0]), reverse=True)
        return matches[0][1], matches[0][0]

from contextvars import ContextVar

_current_perm_session_var: ContextVar[Optional[str]] = ContextVar(
    "current_permission_session_id", default=None
)


def get_current_permission_session() -> str:
    explicit = _current_perm_session_var.get()
    if explicit:
        return explicit
    try:
        from aja.workspace.context import get_current_workspace
        ws = get_current_workspace()
        if ws and getattr(ws, "id", None):
            return f"ws:{ws.id}"
    except Exception:
        pass
    return "default"


def set_current_permission_session(session_id: str) -> Any:
    return _current_perm_session_var.set(session_id)


def reset_current_permission_session(token: Any) -> None:
    _current_perm_session_var.reset(token)


class _SessionGrantsProxy:
    """Provides a backward-compatible set-like interface to active session grants."""

    def add(self, scope: str, ttl_s: float = 3600.0) -> None:
        PermissionEngine.add_session_grant(scope, ttl_s=ttl_s)

    def remove(self, scope: str) -> None:
        PermissionEngine.remove_session_grant(scope)

    def discard(self, scope: str) -> None:
        PermissionEngine.remove_session_grant(scope)

    def clear(self) -> None:
        PermissionEngine.clear_session_grants()

    def __contains__(self, scope: Any) -> bool:
        return PermissionEngine.has_session_grant(str(scope))

    def __iter__(self):
        sid = get_current_permission_session()
        grants = PermissionEngine._grants_store.get(sid, {})
        now = time.monotonic()
        return iter([k for k, exp in grants.items() if exp >= now])

    def __len__(self) -> int:
        return len(list(iter(self)))


class PermissionEngine:
    _grants_store: Dict[str, Dict[str, float]] = {}  # session_id -> {scope: expiry_time}
    _session_grants: _SessionGrantsProxy = _SessionGrantsProxy()
    _ask_lock: threading.Lock = threading.Lock()

    @classmethod
    def clear_session_grants(cls, session_id: Optional[str] = None) -> None:
        if session_id:
            cls._grants_store.pop(session_id, None)
        else:
            cls._grants_store.clear()

    @classmethod
    def add_session_grant(cls, scope: str, session_id: Optional[str] = None, ttl_s: float = 3600.0) -> None:
        sid = session_id or get_current_permission_session()
        now = time.monotonic()
        if sid not in cls._grants_store:
            cls._grants_store[sid] = {}
        cls._grants_store[sid][scope] = now + ttl_s

    @classmethod
    def remove_session_grant(cls, scope: str, session_id: Optional[str] = None) -> None:
        sid = session_id or get_current_permission_session()
        if sid in cls._grants_store:
            cls._grants_store[sid].pop(scope, None)

    @classmethod
    def has_session_grant(cls, scope: str, session_id: Optional[str] = None) -> bool:
        sid = session_id or get_current_permission_session()
        grants = cls._grants_store.get(sid, {})
        now = time.monotonic()
        expired = [k for k, exp in grants.items() if exp < now]
        for k in expired:
            del grants[k]
        return scope in grants or any(_scope_matches(pattern, scope) for pattern in grants)

    def __init__(
        self,
        policy: Optional[PermissionPolicy] = None,
        approval_provider: Optional[Callable[[str, str, float], bool]] = None,
    ):
        self.policy = policy or _policy_from_runtime_config()
        self.approval_provider = approval_provider

    def authorize(
        self,
        scope: str,
        *,
        mission_id: Optional[str] = None,
        journal: Optional[Any] = None,
        dry_run: bool = False,
        reason: str = "",
    ) -> AuthorizationResult:
        decision, matched_scope = self.policy.decision_for(scope)
        grant_id = f"grant-{uuid.uuid4().hex[:12]}"

        # 1. Deny ALWAYS wins over any grants or allowances
        if decision == "deny":
            _emit(journal, "PERMISSION_DENIED", {
                "scope": scope,
                "matched_scope": matched_scope,
                "decision": decision,
                "reason": reason or "Permission policy denied this scope.",
            })
            return AuthorizationResult(False, decision, scope, reason, None, matched_scope)

        # 2. Allow
        if decision == "allow":
            _emit(journal, "PERMISSION_GRANTED", {
                "scope": scope,
                "matched_scope": matched_scope,
                "decision": decision,
                "grant_id": grant_id,
                "reason": reason,
            })
            return AuthorizationResult(True, decision, scope, reason, grant_id, matched_scope)

        # 3. Check session grants for 'ask' scopes
        effective_session = mission_id or get_current_permission_session()
        if self.has_session_grant(scope, session_id=effective_session):
            _emit(journal, "PERMISSION_GRANTED", {
                "scope": scope,
                "matched_scope": matched_scope,
                "decision": "allow",
                "grant_id": grant_id,
                "reason": f"{reason} (session grant)",
            })
            return AuthorizationResult(True, "allow", scope, reason, grant_id, matched_scope)

        _emit(journal, "PERMISSION_REQUESTED", {
            "scope": scope,
            "matched_scope": matched_scope,
            "decision": "ask",
            "grant_id": grant_id,
            "mission_id": mission_id,
            "reason": reason,
            "timeout_s": self.policy.ask_timeout_s,
            "dry_run": dry_run,
        })

        if dry_run:
            _emit(journal, "PERMISSION_DENIED", {
                "scope": scope,
                "matched_scope": matched_scope,
                "decision": "ask",
                "grant_id": grant_id,
                "reason": "Dry-run permission request defaulted to deny.",
            })
            return AuthorizationResult(False, "ask", scope, "Dry-run permission request defaulted to deny.", grant_id, matched_scope)

        approved = self._ask(scope, reason, session_id=effective_session)
        if approved:
            _emit(journal, "PERMISSION_GRANTED", {
                "scope": scope,
                "matched_scope": matched_scope,
                "decision": "ask",
                "grant_id": grant_id,
                "reason": reason,
            })
            return AuthorizationResult(True, "ask", scope, reason, grant_id, matched_scope)

        _emit(journal, "PERMISSION_DENIED", {
            "scope": scope,
            "matched_scope": matched_scope,
            "decision": "ask",
            "grant_id": grant_id,
            "reason": "Permission request timed out or was denied.",
        })
        return AuthorizationResult(False, "ask", scope, "Permission request timed out or was denied.", grant_id, matched_scope)

    def _ask(self, scope: str, reason: str, session_id: Optional[str] = None) -> bool:
        timeout_s = self.policy.ask_timeout_s
        if timeout_s <= 0:
            return False

        effective_session = session_id or get_current_permission_session()
        try:
            from aja.config import CONFIG
            if getattr(CONFIG.swarm_settings, "auto_proceed_local", False):
                return True
        except Exception:
            pass

        with self._ask_lock:
            # Re-check session grants inside lock in case a concurrent worker already obtained approval
            if self.has_session_grant(scope, session_id=effective_session):
                return True

            if self.approval_provider:
                try:
                    res = bool(self.approval_provider(scope, reason, timeout_s))
                    if res:
                        self.add_session_grant(scope, session_id=effective_session)
                    return res
                except Exception:
                    return False

            if not sys.stdin or not sys.stdin.isatty() or os.environ.get("PYTEST_CURRENT_TEST"):
                return False

            try:
                import msvcrt
            except ImportError:
                return False

            prompt = f"\nAllow permission scope '{scope}' for this session? Type y within {timeout_s:.0f}s to approve: "
            print(prompt, end="", flush=True)
            deadline = time.monotonic() + timeout_s
            chars = []
            while time.monotonic() < deadline:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    print(ch, end="", flush=True)
                    if ch in ("\r", "\n"):
                        break
                    chars.append(ch)
                time.sleep(0.05)
            print()
            approved = "".join(chars).strip().lower() in {"y", "yes"}
            if approved:
                self.add_session_grant(scope, session_id=effective_session)
            return approved


class Permission:
    """Backward-compatible facade for older capability checks."""

    def __init__(self, allowed_capabilities: Iterable[str], blocked_commands: Iterable[str]):
        self.allowed_capabilities = set(allowed_capabilities)
        self.blocked_commands = list(blocked_commands)
        self.engine = PermissionEngine()

    def allow(self, tool: str) -> bool:
        if tool in self.allowed_capabilities:
            return True
        return self.engine.policy.decision_for(tool)[0] == "allow"

    def validate_command(self, cmd: str) -> bool:
        for blocked in self.blocked_commands:
            if blocked in cmd:
                return False
        return command_allowed(cmd)


def required_scope_for_shell(cmd: str, classification: Optional[Dict[str, Any]] = None) -> str:
    classification = classification or {}
    decision = classification.get("decision")
    root = (classification.get("root") or "").lower()
    
    # Lightweight defense-in-depth heuristic against path traversal
    if "../" in cmd or "..\\" in cmd or re.search(r"(?:^|\s)(?:/|[a-zA-Z]:\\)", cmd):
        return "shell.destructive"
        
    if decision in {"ask", "deny"}:
        return "shell.destructive"
    if root in {"cat", "type", "dir", "ls", "rg", "grep", "find", "get-content", "get-childitem"}:
        return "shell.read"
    if root in {"echo", "pwd", "cd", "where", "which"}:
        return "shell.read"
    return "shell.write"


def _scope_matches(pattern: str, scope: str) -> bool:
    return pattern == scope or fnmatch.fnmatchcase(scope, pattern)


def _specificity(pattern: str) -> tuple[int, int]:
    return (len(pattern.replace("*", "")), pattern.count("."))


def _emit(journal: Optional[Any], event_type: str, payload: Dict[str, Any]) -> None:
    if journal is None:
        return
    journal.emit(event_type, payload)


def _policy_from_runtime_config() -> PermissionPolicy:
    try:
        from aja.config import CONFIG
        return PermissionPolicy.from_config(getattr(CONFIG, "permission_policy", None))
    except Exception:
        return PermissionPolicy.defaults()


default_permissions = Permission(
    allowed_capabilities=["terminal.exec", "agent.coder", "agent.browser", "shell.*", "python.*"],
    blocked_commands=["shutdown", "mkfs", ":(){:|:&};:", "mv /", "rm /"],
)
