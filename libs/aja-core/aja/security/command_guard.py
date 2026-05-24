import re
from typing import Any, Dict, List

from aja.security.stripper import CommandStripper


DENY_BINARIES = {
    "dd": "Low-level disk writes can irreversibly destroy data.",
    "mkfs": "Filesystem formatting is blocked.",
    "format": "Filesystem formatting is blocked.",
    "diskpart": "Disk partition manipulation is blocked.",
    "bcdedit": "Boot configuration changes are blocked.",
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


def is_known_safe(command: str, root: str, args: List[str]) -> bool:
    lower = command.lower()
    if root in {"python", "python3"}:
        return "--version" in args or lower.startswith(f"{root} -m pip install")
    if root == "npm":
        return any(arg in {"install", "i", "list"} for arg in args)
    if root == "gh":
        return lower.startswith("gh repo view") or lower.startswith("gh issue list")
    if root == "git":
        return True
    if root in {"powershell", "pwsh"}:
        return bool(
            re.search(
                r"\b(get-childitem|get-content|get-process|where-object|select-object)\b",
                command,
                re.IGNORECASE,
            )
        )
    if root == "cmd":
        return bool(re.search(r"\b(dir|type|echo|where)\b", command, re.IGNORECASE))
    if root == "rm":
        safe_targets = ["temp", "tmp", "cache", "node_modules", "dist", "build"]
        return any(t in lower for t in safe_targets) and not ("/" in command and len(command.split("/")) < 3)
    return False


def classify_command(command: str) -> Dict[str, Any]:
    stripper = CommandStripper(command)
    stripper.strip()
    analysis = stripper.report()
    root = (analysis.get("Root Binary") or "").lower()
    args = analysis.get("Argument Tokens", [])
    known_safe = is_known_safe(command, root, args)

    deny_reasons: List[str] = []
    ask_reasons: List[str] = []

    if root in DENY_BINARIES:
        deny_reasons.append(DENY_BINARIES[root])

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

    if analysis.get("Blocked Env Vars"):
        deny_reasons.append(
            "Blocked environment variables detected: "
            + ", ".join(analysis.get("Blocked Env Vars", {}).keys())
            + "."
        )

    if root in ASK_BINARIES and not known_safe:
        ask_reasons.append(ASK_BINARIES[root])

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

    res = {
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
    }

    try:
        from aja.observability.telemetry import log_security_event
        log_security_event(command, res)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Telemetry log failed during command audit: %s", e)

    return res


def command_allowed(command: str) -> bool:
    return classify_command(command)["decision"] != "deny"
