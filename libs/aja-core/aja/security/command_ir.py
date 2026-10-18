"""
command_ir.py - Structured Command Intermediate Representation (Command IR).
=============================================================================

Provides platform-aware tokenization, quote-tracking metacharacter screening,
absolute executable path resolution, and fine-grained verb classification
for autonomous agent command execution.

Design Principles:
- Strict argv-only execution (zero shell=True).
- Pre-parse tripwire: rejects unquoted shell chaining (; & | ` $() < >).
- Dual tokenization: Windows CommandLineToArgvW semantics vs POSIX shlex.
- BatBadBut defense: blocks direct .bat/.cmd execution without safe encapsulation.
- Flag-aware verb policies: prevents read-only tools from executing arbitrary code
  or mutating filesystem (e.g., find -exec, find -delete, git clean).
"""

from __future__ import annotations

import enum
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


class VerbClass(enum.Enum):
    """Classification of command impact and execution safety."""

    READ_ONLY = "read_only"
    WRITE_LOCAL = "write_local"
    NETWORK_EGRESS = "network_egress"
    EXECUTES_CODE = "executes_code"
    DESTRUCTIVE = "destructive"


# Metacharacters that indicate shell chaining, subshells, or redirection
DANGEROUS_UNQUOTED_CHARS = {";", "&", "|", "`", "<", ">", "\n", "\r"}


def scan_unquoted_metacharacters(cmd: str) -> Optional[str]:
    """
    Scan raw command string for shell chaining, subshell substitution, or redirection
    occurring outside quotes.
    Returns the first offending metacharacter/sequence found, or None if safe.
    """
    if not cmd:
        return None

    in_single_quote = False
    in_double_quote = False
    i = 0
    n = len(cmd)

    while i < n:
        c = cmd[i]

        # Escape handling: backslash escapes next char outside single quotes (POSIX)
        if c == "\\" and not in_single_quote and i + 1 < n:
            i += 2
            continue

        if c == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            i += 1
            continue

        if c == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            i += 1
            continue

        # If outside quotes, test for dangerous shell metacharacters
        if not in_single_quote and not in_double_quote:
            # Check for $( subshell syntax
            if c == "$" and i + 1 < n and cmd[i + 1] == "(":
                return "$("

            if c in DANGEROUS_UNQUOTED_CHARS:
                # Group adjacent tokens (e.g., &&, ||, >>) for clearer error messages
                if i + 1 < n and cmd[i + 1] == c:
                    return c + c
                return c

        i += 1

    if in_single_quote or in_double_quote:
        return "unclosed_quote"

    return None


def tokenize_windows(cmd_line: str) -> List[str]:
    """
    Tokenize a Windows command line according to CommandLineToArgvW rules.
    Accurately handles double quotes, escaped quotes, and literal backslashes.
    """
    argv: List[str] = []
    if not cmd_line or not cmd_line.strip():
        return argv

    cur: List[str] = []
    i = 0
    n = len(cmd_line)
    in_quotes = False
    has_token = False

    while i < n:
        c = cmd_line[i]

        if c in (" ", "\t"):
            if in_quotes:
                cur.append(c)
                has_token = True
            elif has_token:
                argv.append("".join(cur))
                cur.clear()
                has_token = False
            i += 1
        elif c == '"':
            has_token = True
            # Count preceding backslashes
            num_backslashes = 0
            j = len(cur) - 1
            while j >= 0 and cur[j] == "\\":
                num_backslashes += 1
                j -= 1

            if num_backslashes > 0:
                # Remove counted backslashes from cur and process them according to MSVCRT rules
                cur = cur[:-num_backslashes]
                # 2n backslashes before quote -> n backslashes, quote acts as toggle
                # 2n+1 backslashes before quote -> n backslashes, quote is literal
                cur.extend(["\\"] * (num_backslashes // 2))
                if num_backslashes % 2 == 1:
                    cur.append('"')
                else:
                    in_quotes = not in_quotes
            else:
                in_quotes = not in_quotes
            i += 1
        elif c == "\\":
            cur.append(c)
            has_token = True
            i += 1
        else:
            cur.append(c)
            has_token = True
            i += 1

    if has_token:
        argv.append("".join(cur))

    return argv


def tokenize_command(cmd: str) -> List[str]:
    """
    Platform-aware command tokenizer. Uses CommandLineToArgvW on Windows,
    and posix shlex on Linux/macOS.
    """
    if os.name == "nt":
        return tokenize_windows(cmd)
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return []


@dataclass
class CommandIR:
    """
    Structured Intermediate Representation of an executable command.
    """

    raw_command: str
    argv: List[str]
    executable: str
    resolved_path: Optional[str]
    verb_class: VerbClass
    is_safe_to_probe: bool
    rejection_reason: Optional[str] = None


# Known safe read-only binaries (free of mutating/executing flags)
SAFE_READ_BINARIES: Set[str] = {
    "cat",
    "head",
    "tail",
    "grep",
    "findstr",
    "dir",
    "where",
    "which",
    "echo",
    "printf",
    "diff",
    "pwd",
}

# Known local write binaries
LOCAL_WRITE_BINARIES: Set[str] = {
    "touch",
    "mkdir",
    "cp",
    "mv",
    "copy",
    "move",
}

# Known network egress binaries
NETWORK_EGRESS_BINARIES: Set[str] = {
    "curl",
    "wget",
    "ssh",
    "scp",
    "ftp",
    "nc",
    "netcat",
}

# Known code execution binaries
CODE_EXEC_BINARIES: Set[str] = {
    "python",
    "python3",
    "node",
    "pytest",
    "cargo",
    "bash",
    "sh",
    "zsh",
    "powershell",
    "pwsh",
    "cmd",
    "ruby",
    "perl",
    "php",
}

# Known destructive binaries
DESTRUCTIVE_BINARIES: Set[str] = {
    "rm",
    "del",
    "rmdir",
    "format",
    "mkfs",
    "dd",
    "fdisk",
    "diskpart",
}


def parse_command_to_ir(
    cmd: str,
    workspace_root: Optional[Path] = None,
    allow_code_exec: bool = False,
) -> CommandIR:
    """
    Parse and validate a raw command string into a structured CommandIR.
    Enforces metacharacter checks, binary resolution, and flag policies.
    """
    raw_cmd = (cmd or "").strip()
    if not raw_cmd:
        return CommandIR(
            raw_command=raw_cmd,
            argv=[],
            executable="",
            resolved_path=None,
            verb_class=VerbClass.DESTRUCTIVE,
            is_safe_to_probe=False,
            rejection_reason="Empty command string",
        )

    # 1. Pre-parse tripwire: reject unquoted shell chaining / redirection
    tripwire_char = scan_unquoted_metacharacters(raw_cmd)
    if tripwire_char:
        return CommandIR(
            raw_command=raw_cmd,
            argv=[],
            executable="",
            resolved_path=None,
            verb_class=VerbClass.DESTRUCTIVE,
            is_safe_to_probe=False,
            rejection_reason=f"Rejected unquoted shell metacharacter '{tripwire_char}'",
        )

    # 2. Tokenize command line into argv list
    argv = tokenize_command(raw_cmd)
    if not argv:
        return CommandIR(
            raw_command=raw_cmd,
            argv=[],
            executable="",
            resolved_path=None,
            verb_class=VerbClass.DESTRUCTIVE,
            is_safe_to_probe=False,
            rejection_reason="Command tokenization resulted in empty argument vector",
        )

    exe_token = argv[0]
    exe_name = Path(exe_token).name.lower()
    # Strip .exe suffix if present
    base_exe = exe_name[:-4] if exe_name.endswith(".exe") else exe_name

    # 3. BatBadBut Defense: block direct .bat / .cmd execution
    if exe_name.endswith((".bat", ".cmd")):
        return CommandIR(
            raw_command=raw_cmd,
            argv=argv,
            executable=exe_token,
            resolved_path=None,
            verb_class=VerbClass.EXECUTES_CODE,
            is_safe_to_probe=False,
            rejection_reason="Direct execution of Windows batch scripts (.bat/.cmd) blocked (BatBadBut defense)",
        )

    # 4. Resolve absolute executable path
    resolved = shutil.which(exe_token)
    canonical_path = os.path.realpath(os.path.normcase(resolved)) if resolved else None

    # 5. Flag-level inspection for binaries with dual personalities
    # 5a. 'find' binary inspection
    if base_exe == "find":
        dangerous_find_flags = {
            "-delete",
            "-exec",
            "-execdir",
            "-ok",
            "-okdir",
            "-fprint",
            "-fprintf",
        }
        for arg in argv[1:]:
            lower_arg = arg.lower()
            if any(lower_arg == f or lower_arg.startswith(f) for f in dangerous_find_flags):
                return CommandIR(
                    raw_command=raw_cmd,
                    argv=argv,
                    executable=exe_token,
                    resolved_path=canonical_path,
                    verb_class=VerbClass.DESTRUCTIVE,
                    is_safe_to_probe=False,
                    rejection_reason=f"Dangerous flag '{arg}' in find command",
                )
        return CommandIR(
            raw_command=raw_cmd,
            argv=argv,
            executable=exe_token,
            resolved_path=canonical_path,
            verb_class=VerbClass.READ_ONLY,
            is_safe_to_probe=True,
        )

    # 5b. 'git' binary inspection
    if base_exe == "git":
        # Check subcommands
        subcommand = ""
        for a in argv[1:]:
            if not a.startswith("-"):
                subcommand = a.lower()
                break

        if subcommand in ("status", "diff", "log", "show", "branch", "rev-parse", "describe"):
            return CommandIR(
                raw_command=raw_cmd,
                argv=argv,
                executable=exe_token,
                resolved_path=canonical_path,
                verb_class=VerbClass.READ_ONLY,
                is_safe_to_probe=True,
            )
        elif subcommand in ("clean", "reset", "rm"):
            return CommandIR(
                raw_command=raw_cmd,
                argv=argv,
                executable=exe_token,
                resolved_path=canonical_path,
                verb_class=VerbClass.DESTRUCTIVE,
                is_safe_to_probe=False,
                rejection_reason=f"Destructive git subcommand '{subcommand}'",
            )
        else:
            return CommandIR(
                raw_command=raw_cmd,
                argv=argv,
                executable=exe_token,
                resolved_path=canonical_path,
                verb_class=VerbClass.WRITE_LOCAL,
                is_safe_to_probe=False,
            )

    # 6. Binary Categorization
    if base_exe in DESTRUCTIVE_BINARIES:
        return CommandIR(
            raw_command=raw_cmd,
            argv=argv,
            executable=exe_token,
            resolved_path=canonical_path,
            verb_class=VerbClass.DESTRUCTIVE,
            is_safe_to_probe=False,
            rejection_reason=f"Destructive binary '{base_exe}'",
        )

    if base_exe in CODE_EXEC_BINARIES:
        return CommandIR(
            raw_command=raw_cmd,
            argv=argv,
            executable=exe_token,
            resolved_path=canonical_path,
            verb_class=VerbClass.EXECUTES_CODE,
            is_safe_to_probe=allow_code_exec,
            rejection_reason=None if allow_code_exec else f"Code execution binary '{base_exe}' requires gating",
        )

    if base_exe in NETWORK_EGRESS_BINARIES:
        return CommandIR(
            raw_command=raw_cmd,
            argv=argv,
            executable=exe_token,
            resolved_path=canonical_path,
            verb_class=VerbClass.NETWORK_EGRESS,
            is_safe_to_probe=False,
            rejection_reason=f"Network egress binary '{base_exe}'",
        )

    if base_exe in LOCAL_WRITE_BINARIES:
        return CommandIR(
            raw_command=raw_cmd,
            argv=argv,
            executable=exe_token,
            resolved_path=canonical_path,
            verb_class=VerbClass.WRITE_LOCAL,
            is_safe_to_probe=False,
        )

    if base_exe in SAFE_READ_BINARIES:
        return CommandIR(
            raw_command=raw_cmd,
            argv=argv,
            executable=exe_token,
            resolved_path=canonical_path,
            verb_class=VerbClass.READ_ONLY,
            is_safe_to_probe=True,
        )

    # Default-Deny: Unknown commands are classified as DESTRUCTIVE/UNSAFE by default
    return CommandIR(
        raw_command=raw_cmd,
        argv=argv,
        executable=exe_token,
        resolved_path=canonical_path,
        verb_class=VerbClass.DESTRUCTIVE,
        is_safe_to_probe=False,
        rejection_reason=f"Unrecognized binary '{base_exe}' not in allowlist (default-deny)",
    )
