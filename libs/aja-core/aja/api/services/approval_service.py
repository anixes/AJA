"""
Extracted from api/bridge.py — Approval lifecycle and execution service.

Pure functions only; no FastAPI imports. Memory access via get_aja_memory()
or injected memory provider. Single-flight locks prevent concurrent double-execution.
"""

import asyncio
from datetime import datetime, timedelta
import threading
import time
from typing import Any, Callable, Optional

from aja.api.services.command_policy import (
    analyze_shell_command as analyze_shell_command_policy,
)
from aja.config import PROJECT_ROOT
from aja.memory.secretary import get_aja_memory


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def compact_text(text: str, limit: int = 1000) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}... [truncated]"


def analyze_shell_command(command: str):
    return analyze_shell_command_policy(command)


def normalize_risk_level(level: str):
    normalized = (level or "").lower()
    if normalized in {"critical", "high"}:
        return "high"
    if normalized == "medium":
        return "medium"
    return "low"


def build_rollback_path(action_type: str, command: str):
    lowered = command.lower()
    if "shutdown /s" in lowered:
        return "Run: shutdown /a before the timer expires."
    if lowered.startswith("git pull"):
        return "Use git reflog to find the previous HEAD, then reset only after reviewing local changes."
    if "jupyter" in lowered or "notebook" in lowered:
        return "Stop the restarted notebook process and relaunch the previous notebook command if needed."
    if action_type == "training_job":
        return "Stop the spawned background process from Task Manager or terminal logs; workspace files are not changed directly by the launcher."
    return "No automatic rollback is known. Review output and restore from version control or backup if needed."


def build_dry_run_summary(action_type: str, command: str):
    if action_type == "git_update":
        return "Would fetch and fast-forward the current repository only if Git can do so without a merge commit."
    if action_type == "scheduled_shutdown":
        return "Would schedule a Windows shutdown timer. It can be canceled before expiry with shutdown /a."
    if action_type == "notebook_restart":
        return "Would stop known Jupyter notebook/lab processes, then start a new notebook process."
    if action_type == "training_job":
        return "Would delegate a background AJA training mission through AJA Core."
    if action_type == "gpu_check":
        return "Would query NVIDIA GPU status with nvidia-smi."
    return f"Would execute: {command}"


def build_approval_object(
    text: str,
    command: str,
    spec: dict,
    classification: dict,
    user_id: int,
    chat_id: int | str,
):
    action_type = spec.get("action_type", "shell_command")
    reasons = [spec.get("reason")] if spec.get("reason") else []
    reasons.extend(classification.get("reasons", []))
    risk_level = normalize_risk_level(
        classification.get("risk_level") or classification.get("level", "MEDIUM")
    )
    if spec.get("risk_level"):
        risk_level = spec["risk_level"]
    request_id = f"approval-{int(time.time())}-{abs(hash((user_id, command, time.time()))) % 10000}"
    expires_at = (datetime.now().astimezone() + timedelta(minutes=10)).isoformat(
        timespec="seconds"
    )
    analysis = classification.get("analysis") or {}
    return {
        "id": request_id,
        "tool": "bash",
        "input": {"command": command},
        "command": command,
        "commandPreview": command,
        "actionType": action_type,
        "rootBinary": analysis.get("Root Binary") or classification.get("root_binary"),
        "level": classification.get("level", "MEDIUM"),
        "riskLevel": risk_level,
        "reasons": [reason for reason in reasons if reason],
        "operatorReason": spec.get("reason")
        or (
            reasons[0]
            if reasons
            else "This action needs operator review before execution."
        ),
        "rollbackPath": build_rollback_path(action_type, command),
        "expiresAt": expires_at,
        "requesterSource": "Telegram",
        "dryRunSummary": build_dry_run_summary(action_type, command),
        "createdAt": now_iso(),
        "telegram": {"userId": user_id, "chatId": chat_id, "text": text},
    }


def format_approval_for_mobile(approval: dict):
    reasons = approval.get("reasons") or []
    reason_text = (
        "\n".join(f"- {reason}" for reason in reasons) or "- Manual review required."
    )
    return "\n".join(
        [
            "Approval request",
            f"ID: {approval.get('id')}",
            f"Action: {approval.get('actionType')}",
            f"Risk: {approval.get('riskLevel', approval.get('level', 'medium'))}",
            f"Source: {approval.get('requesterSource')}",
            f"Expires: {approval.get('expiresAt')}",
            "",
            "Command:",
            approval.get("commandPreview") or approval.get("command") or "(unknown)",
            "",
            "Reason:",
            approval.get("operatorReason") or "Review required.",
            "",
            "Expected effect:",
            approval.get("dryRunSummary") or "No dry-run summary available.",
            "",
            "Rollback:",
            approval.get("rollbackPath") or "No rollback path known.",
            "",
            "Review notes:",
            reason_text,
            "",
            f"Approve: approve {approval.get('id')}",
            f"Reject: reject {approval.get('id')}",
        ]
    )


async def run_shell_command(command: str, timeout: int = 60):
    # Final safety gate: verify command doesn't have a CRITICAL deny decision
    classification = analyze_shell_command(command)
    if classification["decision"] == "deny":
        return {
            "ok": False,
            "code": 1,
            "output": f"Execution blocked: {classification['reasons'][0]}",
        }

    from aja.runtime.execution import ExecutionRequest, get_default_execution_manager

    result = await get_default_execution_manager().run(
        ExecutionRequest(
            command=command,
            cwd=str(PROJECT_ROOT),
            timeout=timeout,
            workspace_mode="direct",
            metadata={"legacy_api": "api.bridge.run_shell_command"},
        )
    )
    output = result.stdout.strip()
    if result.stderr.strip():
        output = f"{output}\nErrors:\n{result.stderr.strip()}".strip()
    return {
        "ok": result.success,
        "code": result.exit_code,
        "output": compact_text(output),
    }


async def run_file_guardian_check(command: str):
    """Python-native command safety check replacing the removed TypeScript file guardian.

    Delegates to the same ``classify_command`` engine used by CommandGuard so
    the decision logic is consistent across all execution paths.  Returns a dict
    with a ``decision`` key of ``"ALLOW"`` or ``"DENY"`` to preserve the
    interface expected by callers.
    """
    from aja.security.command_guard import classify_command as _classify

    try:
        result = _classify(command)
        raw_decision = result.get("decision", "deny")
        # Map classify_command decisions → file-guardian style uppercase decision.
        # "allow" → ALLOW; "ask" or "deny" → DENY (conservative default).
        decision = "ALLOW" if raw_decision == "allow" else "DENY"
        return {
            "decision": decision,
            "level": result.get("level", "UNKNOWN"),
            "reasons": result.get("reasons", []),
        }
    except Exception as exc:
        return {"decision": "DENY", "error": str(exc)}


def get_pending_approval_by_id(request_id: str, memory_provider: Optional[Callable[[], Any]] = None):
    """Look up an approval by ID from LanceDB (single source of truth)."""
    mem = memory_provider() if memory_provider else get_aja_memory()
    row = mem.get_approval(request_id)
    if row and row.get("status") == "pending":
        return row
    return None


def approval_is_expired(approval: dict):
    expires_at = approval.get("expiresAt") or approval.get("expires_at")
    if not expires_at:
        return False
    try:
        return (
            datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp()
            <= time.time()
        )
    except Exception:
        return True


_APPROVAL_CLAIM_LOCKS: dict[str, asyncio.Lock] = {}
_APPROVAL_CLAIM_LOCKS_GUARD = threading.Lock()


def _approval_claim_lock(request_id: str) -> asyncio.Lock:
    """Single-flight lock so concurrent resolvers cannot double-execute."""
    with _APPROVAL_CLAIM_LOCKS_GUARD:
        lock = _APPROVAL_CLAIM_LOCKS.get(request_id)
        if lock is None:
            lock = asyncio.Lock()
            _APPROVAL_CLAIM_LOCKS[request_id] = lock
        return lock


async def approve_runtime_approval(
    request_id: str,
    user_id: int | None = None,
    memory_provider: Optional[Callable[[], Any]] = None,
    guardian_runner: Optional[Callable[[str], Any]] = None,
    shell_runner: Optional[Callable[[str], Any]] = None,
    timeout: int = 60,
):
    async with _approval_claim_lock(request_id):
        return await _approve_runtime_approval_locked(
            request_id,
            user_id,
            memory_provider=memory_provider,
            guardian_runner=guardian_runner,
            shell_runner=shell_runner,
            timeout=timeout,
        )


async def _approve_runtime_approval_locked(
    request_id: str,
    user_id: int | None = None,
    memory_provider: Optional[Callable[[], Any]] = None,
    guardian_runner: Optional[Callable[[str], Any]] = None,
    shell_runner: Optional[Callable[[str], Any]] = None,
    timeout: int = 60,
):
    approval = get_pending_approval_by_id(request_id, memory_provider=memory_provider)
    if not approval:
        return {"ok": False, "message": "No pending approval found for that id."}
    if user_id is not None:
        telegram_meta = approval.get("telegram_meta") or {}
        telegram_user = (
            telegram_meta.get("userId")
            or approval.get("user_id")
        )
        if telegram_user is not None and int(telegram_user) != int(user_id):
            return {
                "ok": False,
                "message": "That approval belongs to a different Telegram user.",
            }
    mem = memory_provider() if memory_provider else get_aja_memory()
    if approval_is_expired(approval):
        mem.update_approval(request_id, "expired", "Expired without action.")
        mem.log_approval_audit(
            {
                "approval_id": request_id,
                "action": "expired",
                "requester_source": approval.get("requester_source"),
                "command": approval.get("command"),
            }
        )
        return {"ok": False, "message": "Approval expired. Send the command again."}

    command = approval.get("command")
    if not command:
        return {"ok": False, "message": "Approval has no executable command."}

    if guardian_runner is not None:
        guardian_res = guardian_runner(command)
        file_guardian = await guardian_res if asyncio.iscoroutine(guardian_res) else guardian_res
    else:
        file_guardian = await run_file_guardian_check(command)
    classification = analyze_shell_command(command)
    if file_guardian["decision"] == "DENY" or classification["decision"] == "deny":
        mem.update_approval(request_id, "blocked", "Blocked at execution re-check.")
        reasons = classification.get("reasons", [])
        if file_guardian.get("error"):
            reasons.append(file_guardian["error"])
        mem.log_approval_audit(
            {
                "approval_id": request_id,
                "action": "blocked_at_execution",
                "command": command,
                "reasons": reasons,
            }
        )
        return {
            "ok": False,
            "message": "Approval blocked at execution re-check: "
            + "; ".join(reasons or ["FileGuardian denied the command."]),
        }

    # Claim the approval BEFORE any side effects so concurrent resolvers
    # observe a non-pending row and cannot execute the command twice.
    mem.update_approval(request_id, "executing", "Claimed for execution.")
    mem.log_approval_audit(
        {
            "approval_id": request_id,
            "action": "approved",
            "requester_source": approval.get("requester_source"),
            "command": command,
        }
    )
    try:
        if shell_runner is not None:
            shell_res = shell_runner(command)
            result = await shell_res if asyncio.iscoroutine(shell_res) else shell_res
        else:
            result = await run_shell_command(command, timeout=timeout)
    except Exception as exc:
        # Roll the claim back to a terminal failed state (never re-pending).
        mem.update_approval(request_id, "failed", compact_text(str(exc), 300))
        mem.log_approval_audit(
            {
                "approval_id": request_id,
                "action": "execution_failed",
                "command": command,
            }
        )
        return {"ok": False, "message": f"Failed: execution error\n{exc}"}
    mem.update_approval(
        request_id,
        "resolved" if result["ok"] else "failed",
        compact_text(result["output"], 300),
    )
    mem.add_runtime_event(
        {
            "event_type": "APPROVED" if result["ok"] else "DENY",
            "tool": approval.get("tool", "bash"),
            "message": compact_text(result["output"], 500),
            "command": command,
            "root_binary": approval.get("root_binary"),
            "level": approval.get("level"),
        }
    )
    mem.log_approval_audit(
        {
            "approval_id": request_id,
            "action": "executed" if result["ok"] else "execution_failed",
            "exit_code": result["code"],
            "command": command,
        }
    )
    prefix = "OK" if result["ok"] else f"Failed ({result['code']})"
    return {
        "ok": result["ok"],
        "message": f"{prefix}: {approval.get('action_type', 'action')}\n{result['output']}",
    }


def reject_runtime_approval(
    request_id: str,
    user_id: int | None = None,
    memory_provider: Optional[Callable[[], Any]] = None,
):
    approval = get_pending_approval_by_id(request_id, memory_provider=memory_provider)
    if not approval:
        return {"ok": False, "message": "No pending approval found for that id."}
    if user_id is not None:
        telegram_meta = approval.get("telegram_meta") or {}
        telegram_user = telegram_meta.get("userId") or approval.get("user_id")
        if telegram_user is not None and int(telegram_user) != int(user_id):
            return {
                "ok": False,
                "message": "That approval belongs to a different Telegram user.",
            }

    mem = memory_provider() if memory_provider else get_aja_memory()
    mem.update_approval(request_id, "rejected", "Rejected by operator.")
    mem.log_approval_audit(
        {
            "approval_id": request_id,
            "action": "rejected",
            "requester_source": approval.get("requester_source"),
            "command": approval.get("command"),
        }
    )
    mem.add_runtime_event(
        {
            "event_type": "DENIED",
            "tool": approval.get("tool", "bash"),
            "message": f"Rejected approval {request_id}.",
            "command": approval.get("command"),
            "root_binary": approval.get("root_binary"),
            "level": approval.get("level"),
        }
    )
    return {"ok": True, "message": f"Rejected approval {request_id}."}
