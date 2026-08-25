import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from aja.security.stripper import CommandStripper

logger = logging.getLogger(__name__)


DENY_BINARIES = {
    "dd": "Low-level disk writes can irreversibly destroy data.",
    "mkfs": "Filesystem formatting is blocked.",
    "format": "Filesystem formatting is blocked.",
    "diskpart": "Disk partition manipulation is blocked.",
    "bcdedit": "Boot configuration changes are blocked.",
    "fdisk": "Disk partition manipulation is blocked.",
    "parted": "Disk partition manipulation is blocked.",
    "gparted": "Disk partition manipulation is blocked.",
}

ASK_BINARIES = {
    "shutdown": "System shutdown requires confirmation.",
    "reboot": "System restart requires confirmation.",
    "taskkill": "Process termination requires confirmation.",
    "kill": "Process termination requires confirmation.",
    "pkill": "Process termination requires confirmation.",
    "chmod": "Permission changes require confirmation.",
    "chown": "Ownership changes require confirmation.",
    "powershell": "PowerShell execution requires confirmation.",
    "pwsh": "PowerShell execution requires confirmation.",
    "cmd": "cmd.exe execution requires confirmation.",
    "python": "Interpreter execution can run arbitrary code.",
    "python3": "Interpreter execution can run arbitrary code.",
    "node": "Interpreter execution can run arbitrary code.",
    "bash": "Shell execution requires confirmation.",
    "sh": "Shell execution requires confirmation.",
    "zsh": "Shell execution requires confirmation.",
    "rm": "Deletion commands require confirmation.",
    "mv": "Move commands can overwrite data.",
    "git": "Git commands can mutate the workspace.",
    "gh": "GitHub CLI commands can mutate remote state.",
    "npm": "Package manager commands can mutate the workspace.",
    "pnpm": "Package manager commands can mutate the workspace.",
    "yarn": "Package manager commands can mutate the workspace.",
    "curl": "Network downloads require review.",
    "wget": "Network downloads require review.",
}

DENY_PATTERNS = {
    "network-pipe": "Piping network output directly into an interpreter is blocked.",
    "ssh-write": "Writing directly into SSH trust material is blocked.",
    "system-path-write": "Redirecting output into protected system paths is blocked.",
    "command-substitution": "Shell substitution syntax can hide unsafe behavior.",
    "unbalanced-shell-syntax": "Command parsing failed due to invalid shell syntax.",
    "root-deletion": "Root filesystem destructive deletion is strictly blocked.",
    "fork-bomb": "Fork bomb execution is strictly blocked.",
}

ASK_PATTERNS = {
    "protected-path": "The command targets a protected path.",
    "path-traversal": "The command uses parent-directory traversal.",
    "recursive-delete-flag": "The command includes recursive destructive flags.",
}

WINDOWS_DENY_PATTERNS = {
    "format-volume": re.compile(r"\bformat-volume\b|\bformat\b\s+[a-z]:", re.IGNORECASE),
    "registry-write": re.compile(r"\breg(?:\.exe)?\b\s+(?:add|delete|import)\b", re.IGNORECASE),
}

WINDOWS_ASK_PATTERNS = {
    "remove-item-force-recurse": re.compile(
        r"\bremove-item\b(?=.*(?:^|\s)-(?:recurse|r)\b)(?=.*(?:^|\s)-(?:force|f)\b)",
        re.IGNORECASE,
    ),
    "stop-process-force": re.compile(r"\bstop-process\b.*\b-(?:force|f)\b", re.IGNORECASE),
    "cmd-recursive-delete": re.compile(r"\b(?:del|rmdir|rd)\b.*\s/[sq]\b", re.IGNORECASE),
    "bypass-execution-policy": re.compile(r"\s-executionpolicy\s+bypass\b", re.IGNORECASE),
}

WINDOWS_PATTERN_REASONS = {
    "format-volume": "Filesystem formatting is blocked.",
    "registry-write": "Registry writes are blocked.",
    "remove-item-force-recurse": "Recursive forced PowerShell deletion requires confirmation.",
    "stop-process-force": "Forced process termination requires confirmation.",
    "cmd-recursive-delete": "Recursive cmd deletion requires confirmation.",
    "bypass-execution-policy": "Bypassing PowerShell execution policy requires confirmation.",
}

# Interpreters that can execute inline scripts past via -c/-Command style flags.
# Quoted payloads are invisible to the compound-operator scan (quotes are
# stripped before it runs), so these flags always disqualify the known-safe
# fast path and the inner payload is classified as its own segment(s).
INTERPRETER_ROOTS = {
    "pwsh", "powershell", "bash", "sh", "zsh", "fish",
    "python", "python3", "cmd", "node", "perl", "ruby",
}
INLINE_SCRIPT_FLAGS = {
    "-c", "--command", "-command", "/c", "-encodedcommand", "--encodedcommand",
}


def _has_inline_script_flag(root: str, args: List[str]) -> bool:
    if root not in INTERPRETER_ROOTS:
        return False
    return any(a.lower() in INLINE_SCRIPT_FLAGS for a in args)


_PS_READONLY_CMDLET_RE = re.compile(
    r"\b(get-childitem|get-content|get-process|where-object|select-object"
    r"|get-wmiobject|get-ciminstance|systeminfo|tasklist|netstat|ping|ipconfig)\b",
    re.IGNORECASE,
)
_PS_DESTRUCTIVE_CMDLET_RE = re.compile(
    r"\b(remove-item|del|rmdir|rd|format|start-process|stop-process"
    r"|invoke-expression|invoke-webrequest|invoke-restmethod|set-item"
    r"|new-item|clear-content|taskkill|shutdown|restart-computer"
    r"|remove-itemproperty|set-executionpolicy)\b",
    re.IGNORECASE,
)


def _inner_payload_allows_fast_path(root: str, command: str) -> bool:
    """Decides whether an interpreter's inline-script payload may keep the
    known-safe fast path.

    Statement separators (`;`, `&&`, `||`, newlines, backticks) inside the
    payload are the laundering vector — they disqualify immediately. A single
    read-only pipeline (e.g. `Get-Process | Select-Object -First 5`) stays
    eligible; destructive cmdlets anywhere in the payload disqualify.
    """
    inner = _extract_inline_script(command)
    if inner is None:
        return False
    stripped = inner.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("\"", "'"):
        stripped = stripped[1:-1]
    if any(op in stripped for op in (";", "&&", "||", "\n", "\r", "`")):
        return False
    if root in ("powershell", "pwsh"):
        return bool(_PS_READONLY_CMDLET_RE.search(stripped)) and not bool(
            _PS_DESTRUCTIVE_CMDLET_RE.search(stripped)
        )
    if root in ("python", "python3"):
        # Arbitrary code execution — never fast-path.
        return False
    # cmd /c, bash -c with a single operator-free command: fall through to
    # the generic checks (workspace boundaries etc.). The payload is also
    # expanded into its own classified segments by classify_command.
    return True


def is_known_safe(command: str, root: str, args: List[str]) -> bool:
    # Interpreters with inline scripts (-c/-Command) can hide arbitrary
    # payloads inside quotes, which the operator scan below strips out.
    # The inner payload must be separator-free and read-only to keep the
    # fast path; anything else runs through full classification (and the
    # payload is expanded into its own segments by classify_command).
    if _has_inline_script_flag(root, args) and not _inner_payload_allows_fast_path(root, command):
        return False
    # Chained or compound commands must run through full safety checks
    # and cannot be bypassed via safe roots like 'git' or 'npm'.
    # Remove quoted strings to avoid false positives (e.g. semicolons in commit messages)
    temp = re.sub(r'"[^"]*"|\'[^\']*\'', ' ', command)
    # Any input/output redirection disqualifies the fast-path: redirect targets
    # must be inspected by the full protected-path checks, not skipped.
    if re.search(r"[0-9]*[<>]{1,2}[&0-9]*", temp):
        return False
    temp = re.sub(r"-", " ", temp)
    # Check for compound/chaining operators
    if any(op in temp for op in [';', '&&', '||', '|', '&', '\n', '\r', '`']):
        return False

    lower = command.lower()
    if root in {"python", "python3"}:
        return "--version" in args or lower.startswith(f"{root} -m pip install")
    if root == "npm":
        return any(arg in {"install", "i", "list"} for arg in args)
    if root == "gh":
        return lower.startswith("gh repo view") or lower.startswith("gh issue list")
    if root == "git":
        # Strict allowlist: only common safe subcommands; -c config injection is denied.
        if any(a == "-c" for a in args):
            return False
        subcommand = next((a for a in args if not a.startswith("-")), "")
        return subcommand.lower() in {
            "status", "log", "diff", "show", "branch", "add", "commit",
            "push", "pull", "fetch", "checkout", "stash", "tag", "remote",
            "clone", "init",
        }
    if root in {"powershell", "pwsh"}:
        # Read-only inspection cmdlets only. Process spawning and network
        # execution cmdlets (start-process, invoke-webrequest, ...) are excluded.
        return bool(
            re.search(
                r"\b(get-childitem|get-content|get-process|where-object|select-object|get-wmiobject|get-ciminstance|systeminfo|tasklist|netstat|ping|ipconfig)\b",
                command,
                re.IGNORECASE,
            )
        )
    if root == "cmd":
        return bool(re.search(r"\b(dir|type|echo|where)\b", command, re.IGNORECASE))
    if root == "rm":
        # Every non-flag operand must target a known-safe directory pattern,
        # matched against exact alphanumeric tokens (so "temporal-data" does
        # not satisfy the "temp" rule).
        safe_targets = {"temp", "tmp", "cache", "node_modules", "dist", "build"}
        operands = [a for a in args if not a.startswith("-")]
        if not operands:
            return False
        for operand in operands:
            tokens = {t.lower() for t in re.split(r"[^a-zA-Z0-9]+", operand) if t}
            if not (tokens & safe_targets):
                return False
        return True


def split_compound_command(command: str) -> List[str]:
    """
    Splits a shell command by top-level compound operators (&&, ||, ;, |),
    ignoring operators inside single or double quotes.
    """
    segments: List[str] = []
    current: List[str] = []
    in_single = False
    in_double = False
    escaped = False
    i = 0
    n = len(command)

    while i < n:
        char = command[i]

        if escaped:
            current.append(char)
            escaped = False
            i += 1
            continue

        if char == '\\':
            escaped = True
            current.append(char)
            i += 1
            continue

        if char == "'" and not in_double:
            in_single = not in_single
            current.append(char)
            i += 1
            continue

        if char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
            i += 1
            continue

        if not in_single and not in_double:
            if i + 1 < n and command[i:i+2] in ("&&", "||"):
                seg = "".join(current).strip()
                if seg:
                    segments.append(seg)
                current = []
                i += 2
                continue
            elif char in (";", "|", "&") or char in ("\n", "\r"):
                # Newlines are segment separators too: newline-chained
                # commands must never collapse into one segment whose root
                # hides the trailing binaries from classification.
                seg = "".join(current).strip()
                if seg:
                    segments.append(seg)
                current = []
                i += 1
                continue

        current.append(char)
        i += 1

    last_seg = "".join(current).strip()
    if last_seg:
        segments.append(last_seg)

    return segments if segments else [command]


def _classify_single(command: str) -> Dict[str, Any]:
    stripper = CommandStripper(command)
    stripper.strip()
    analysis = stripper.report()
    raw_root = (analysis.get("Root Binary") or "").lower()
    try:
        from pathlib import Path as _Path
        root = _Path(raw_root).name.lower().replace(".exe", "") if raw_root else ""
    except Exception:
        root = raw_root

    args = analysis.get("Argument Tokens", [])
    known_safe = is_known_safe(command, root, args)

    deny_reasons: List[str] = []
    ask_reasons: List[str] = []

    if root in DENY_BINARIES:
        deny_reasons.append(DENY_BINARIES[root])
    elif "." in root and root.split(".")[0] in DENY_BINARIES:
        deny_reasons.append(DENY_BINARIES[root.split(".")[0]])

    for pattern in analysis.get("Dangerous Patterns", []):
        if pattern in DENY_PATTERNS:
            deny_reasons.append(DENY_PATTERNS[pattern])
        elif pattern in ASK_PATTERNS:
            ask_reasons.append(ASK_PATTERNS[pattern])

    for name, pattern in WINDOWS_DENY_PATTERNS.items():
        if pattern.search(command):
            deny_reasons.append(WINDOWS_PATTERN_REASONS[name])

    for name, pattern in WINDOWS_ASK_PATTERNS.items():
        if pattern.search(command):
            ask_reasons.append(WINDOWS_PATTERN_REASONS[name])

    try:
        from aja.config import CONFIG, PROJECT_ROOT
        from aja.workspace.context import get_current_workspace
        
        ctx = get_current_workspace()
        active_root = str(ctx.path.resolve() if ctx else PROJECT_ROOT.resolve())

        if ctx:
            if "allow_out_of_bounds_paths" in ctx.config_overrides:
                allow_oob = bool(ctx.config_overrides["allow_out_of_bounds_paths"])
            else:
                allow_oob = getattr(CONFIG.swarm_settings, "allow_out_of_bounds_paths", False)
        else:
            allow_oob = getattr(CONFIG.swarm_settings, "allow_out_of_bounds_paths", True)

        if not allow_oob:
            if re.search(r"\.\.[/\\]", command):
                deny_reasons.append("Path traversal (../) is blocked when out-of-bounds paths are disabled.")
            
            safe_system_prefixes = [
                active_root,
                sys.executable,
                sys.prefix,
                getattr(sys, "base_prefix", sys.prefix),
                getattr(sys, "exec_prefix", sys.prefix),
            ]
            if not ctx:
                safe_system_prefixes.append(str(PROJECT_ROOT.resolve()))

            for token in args:
                # Ignore Windows switch flags like /c, /s, /q
                if os.name == "nt" and re.match(r"^/[a-zA-Z]$", token):
                    continue

                if re.match(r"^[a-zA-Z]:[\\/]", token) or (token.startswith("/") and not token.startswith("/dev/")):
                    # Check canonical resolved path if path exists
                    try:
                        resolved_token = str(Path(token).resolve())
                    except Exception:
                        resolved_token = token

                    is_safe = any(
                        token.lower().startswith(prefix.lower()) or resolved_token.lower().startswith(prefix.lower())
                        for prefix in safe_system_prefixes
                        if prefix
                    )
                    if not is_safe:
                        ask_reasons.append("Absolute paths outside the workspace root are flagged when out-of-bounds paths are disabled.")
                        break
    except Exception as e:
        # Fail closed: if the workspace boundary cannot be verified, deny.
        logger.warning("Workspace boundary check failed; failing closed: %s", e)
        deny_reasons.append("Workspace boundary verification failed; command denied for safety.")


    if analysis.get("Blocked Env Vars"):
        deny_reasons.append(
            "Blocked environment variables detected: "
            + ", ".join(analysis.get("Blocked Env Vars", {}).keys())
            + "."
        )

    if root in ASK_BINARIES and not known_safe:
        ask_reasons.append(ASK_BINARIES[root])

    if _has_inline_script_flag(root, args) and not known_safe:
        ask_reasons.append(
            "Interpreter invoked with an inline script flag (-c/-Command); "
            "quoted payloads bypass operator scanning and require confirmation."
        )

    if analysis.get("Operators") and not known_safe:
        ask_reasons.append("Compound shell operators require explicit confirmation.")

    if deny_reasons:
        decision = "deny"
        level = "CRITICAL"
        reasons = deny_reasons
    elif ask_reasons:
        decision = "ask"
        level = "HIGH" if root in {"shutdown", "reboot", "taskkill"} or any("Recursive" in r for r in ask_reasons) else "MEDIUM"
        reasons = ask_reasons
    else:
        decision = "allow"
        level = "LOW"
        reasons = []

    return {
        "decision": decision,
        "level": level,
        "risk_level": level,
        "root": root,
        "root_binary": root,
        "args": args,
        "needs_analysis": decision != "allow",
        "reasons": reasons,
        "analysis": analysis,
        "stripper_report": analysis,
        "known_safe": known_safe,
    }


def _inline_script_info(segment: str) -> Optional[tuple]:
    """Returns (root, raw_inner_payload) when the segment is an interpreter
    invocation with an inline-script flag; else None."""
    import shlex

    try:
        tokens = shlex.split(segment, posix=False)
    except ValueError:
        return None
    if not tokens:
        return None
    root = tokens[0].replace(".exe", "").lower()
    if root not in INTERPRETER_ROOTS:
        return None
    for i, tok in enumerate(tokens[1:], start=1):
        low = tok.lower()
        if low in INLINE_SCRIPT_FLAGS:
            rest = tokens[i + 1:]
            return (root, " ".join(rest) if rest else None)
    return None


def _extract_inline_script(segment: str) -> Optional[str]:
    info = _inline_script_info(segment)
    return info[1] if info else None


def _expand_inline_scripts(segments: List[str]) -> List[str]:
    """Expands SUSPICIOUS interpreter payloads into classifiable segments.

    Escalation-only: when the inner payload qualifies for the fast path
    (separator-free, read-only), no expansion happens — the outer segment's
    own classification governs. When it does not qualify (chaining
    operators, destructive cmdlets, arbitrary code), the inner payload is
    appended as segments so its content produces explicit deny/ask reasons.
    """
    expanded: List[str] = []
    for seg in segments:
        expanded.append(seg)
        info = _inline_script_info(seg)
        if not info or not info[1]:
            continue
        root, inner = info
        if _inner_payload_allows_fast_path(root, seg):
            continue
        stripped = inner.strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("\"", "'"):
            stripped = stripped[1:-1]
        if stripped.strip():
            expanded.extend(split_compound_command(stripped.strip()))
    return expanded


def classify_command(command: str) -> Dict[str, Any]:
    segments = _expand_inline_scripts(split_compound_command(command))

    if len(segments) <= 1:
        res = _classify_single(command)
    else:
        # Check global dangerous patterns across entire raw command first
        stripper = CommandStripper(command)
        stripper.strip()
        analysis = stripper.report()
        global_deny: List[str] = []
        for pattern in analysis.get("Dangerous Patterns", []):
            if pattern in DENY_PATTERNS:
                global_deny.append(DENY_PATTERNS[pattern])

        all_deny_reasons: List[str] = list(global_deny)
        all_ask_reasons: List[str] = []
        all_known_safe = True
        segment_results = []

        for seg in segments:
            sub_res = _classify_single(seg)
            segment_results.append(sub_res)
            if not sub_res.get("known_safe", False):
                all_known_safe = False
            if sub_res["decision"] == "deny":
                all_deny_reasons.extend(sub_res["reasons"])
            elif sub_res["decision"] == "ask":
                all_ask_reasons.extend(sub_res["reasons"])

        if all_deny_reasons:
            decision = "deny"
            level = "CRITICAL"
            reasons = list(dict.fromkeys(all_deny_reasons))
        elif all_ask_reasons or not all_known_safe:
            decision = "ask"
            # Mirror _classify_single's escalation: destructive/recursive
            # payloads and high-risk roots are HIGH even in compound chains.
            high_risk = any(
                "Recursive" in r or "shutdown" in r.lower() or "reboot" in r.lower()
                or "taskkill" in r.lower()
                for r in all_ask_reasons
            ) or any(
                s.get("root") in {"shutdown", "reboot", "taskkill"}
                for s in segment_results
            )
            level = "HIGH" if high_risk else "MEDIUM"
            if not all_ask_reasons and not all_known_safe:
                all_ask_reasons.append("Compound shell chain contains unverified sub-commands.")
            reasons = list(dict.fromkeys(all_ask_reasons))
        else:
            decision = "allow"
            level = "LOW"
            reasons = []

        res = {
            "decision": decision,
            "level": level,
            "risk_level": level,
            "root": segment_results[0]["root"] if segment_results else "",
            "root_binary": segment_results[0]["root_binary"] if segment_results else "",
            "args": [arg for s in segment_results for arg in s.get("args", [])],
            "needs_analysis": decision != "allow",
            "reasons": reasons,
            "analysis": analysis,
            "stripper_report": analysis,
            "compound_segments": [s["root"] for s in segment_results],
        }

    try:
        from aja.observability.telemetry import log_security_event
        log_security_event(command, res)
    except Exception as e:
        logger.debug("Telemetry log failed during command audit: %s", e)

    return res


def command_allowed(command: str) -> bool:
    return classify_command(command)["decision"] != "deny"


def validate_activity(activity: Any) -> Dict[str, Any]:
    """
    Validate an Activity before execution.
    Non-shell activities always pass. Shell activities run through classify_command.
    """
    from aja.orchestration.activity_rt import ActivityType
    if activity.activity_type != ActivityType.SHELL:
        return {"decision": "allow", "reasons": []}
    cmd = activity.args.get("cmd", "")
    return classify_command(cmd)

