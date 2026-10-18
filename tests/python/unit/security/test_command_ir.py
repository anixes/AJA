"""
test_command_ir.py - Unit Tests for Command IR and Quote-Aware Shell Parser.
===========================================================================
Validates:
- Quote-aware pre-scanner: flags unquoted metacharacters, permits quoted literals.
- Tokenizer: Windows CommandLineToArgvW handling of quotes, spaces, and backslashes.
- Verb classification: granular classification of safe vs mutating vs code exec.
- Dangerous flag policies: find -exec/-delete and git clean/reset detection.
- BatBadBut defense: blocks raw Windows batch scripts (.bat/.cmd).
- Default-deny policy for unrecognized binaries.
"""

import pytest
from pathlib import Path
from aja.security.command_ir import (
    CommandIR,
    VerbClass,
    parse_command_to_ir,
    scan_unquoted_metacharacters,
    tokenize_command,
    tokenize_windows,
)


def test_scan_unquoted_metacharacters():
    """Verify tripwire catches dangerous unquoted characters while allowing quoted literals."""
    # Dangerous unquoted chaining must be caught
    assert scan_unquoted_metacharacters("echo hello; rm -rf /") == ";"
    assert scan_unquoted_metacharacters("cat file.txt && echo done") == "&&"
    assert scan_unquoted_metacharacters("ls | grep foo") == "|"
    assert scan_unquoted_metacharacters("echo `whoami`") == "`"
    assert scan_unquoted_metacharacters("echo $(id)") == "$("
    assert scan_unquoted_metacharacters("cat file > out.txt") == ">"
    assert scan_unquoted_metacharacters("cat < input.txt") == "<"

    # Quoted metacharacters must be allowed (benign regex, patterns, strings)
    assert scan_unquoted_metacharacters('grep "a|b" file.py') is None
    assert scan_unquoted_metacharacters("echo 'hello; world'") is None
    assert scan_unquoted_metacharacters('git commit -m "fix: add foo & bar"') is None
    assert scan_unquoted_metacharacters('python -c "print(1 > 0)"') is None


def test_tokenize_windows_quotes_and_paths():
    """Verify Windows tokenizer handles quotes, paths with spaces, and backslashes."""
    # Basic tokenization with spaces inside quotes
    tokens = tokenize_windows('python script.py "first argument with spaces" arg2')
    assert tokens == ["python", "script.py", "first argument with spaces", "arg2"]

    # Windows paths with backslashes
    tokens_path = tokenize_windows(r'cat "C:\Program Files\AJA\config.json" D:\data\log.txt')
    assert tokens_path == ["cat", r"C:\Program Files\AJA\config.json", r"D:\data\log.txt"]


def test_command_ir_safe_read_commands():
    """Verify standard read-only commands receive READ_ONLY verb class and are safe to probe."""
    for cmd in ["cat file.txt", "head -n 10 file.txt", "tail -n 5 log.txt", "grep foo main.py", "dir", "where python"]:
        ir = parse_command_to_ir(cmd)
        assert ir.verb_class == VerbClass.READ_ONLY, f"Expected READ_ONLY for: {cmd}"
        assert ir.is_safe_to_probe is True
        assert ir.rejection_reason is None


def test_command_ir_find_dangerous_flags():
    """Verify find command with -delete or -exec is classified as DESTRUCTIVE/unsafe."""
    safe_find = parse_command_to_ir('find . -name "*.py"')
    assert safe_find.verb_class == VerbClass.READ_ONLY
    assert safe_find.is_safe_to_probe is True

    unsafe_delete = parse_command_to_ir("find . -name temp -delete")
    assert unsafe_delete.verb_class == VerbClass.DESTRUCTIVE
    assert unsafe_delete.is_safe_to_probe is False
    assert "Dangerous flag" in (unsafe_delete.rejection_reason or "")

    unsafe_exec = parse_command_to_ir("find . -exec rm {} +")
    assert unsafe_exec.verb_class == VerbClass.DESTRUCTIVE
    assert unsafe_exec.is_safe_to_probe is False


def test_command_ir_git_subcommands():
    """Verify git subcommands are differentiated between READ_ONLY, WRITE_LOCAL, and DESTRUCTIVE."""
    status_ir = parse_command_to_ir("git status")
    assert status_ir.verb_class == VerbClass.READ_ONLY
    assert status_ir.is_safe_to_probe is True

    diff_ir = parse_command_to_ir("git diff HEAD~1")
    assert diff_ir.verb_class == VerbClass.READ_ONLY
    assert diff_ir.is_safe_to_probe is True

    add_ir = parse_command_to_ir("git add file.py")
    assert add_ir.verb_class == VerbClass.WRITE_LOCAL
    assert add_ir.is_safe_to_probe is False

    clean_ir = parse_command_to_ir("git clean -fd")
    assert clean_ir.verb_class == VerbClass.DESTRUCTIVE
    assert clean_ir.is_safe_to_probe is False

    reset_ir = parse_command_to_ir("git reset --hard")
    assert reset_ir.verb_class == VerbClass.DESTRUCTIVE
    assert reset_ir.is_safe_to_probe is False


def test_command_ir_code_execution_and_destructive():
    """Verify code execution and destructive commands are gated."""
    py_ir = parse_command_to_ir("python test.py")
    assert py_ir.verb_class == VerbClass.EXECUTES_CODE
    assert py_ir.is_safe_to_probe is False  # Unless allow_code_exec=True

    py_allowed = parse_command_to_ir("python test.py", allow_code_exec=True)
    assert py_allowed.verb_class == VerbClass.EXECUTES_CODE
    assert py_allowed.is_safe_to_probe is True

    rm_ir = parse_command_to_ir("rm -rf /")
    assert rm_ir.verb_class == VerbClass.DESTRUCTIVE
    assert rm_ir.is_safe_to_probe is False


def test_command_ir_batbadbut_defense():
    """Verify direct invocation of Windows batch files (.bat/.cmd) is blocked."""
    bat_ir = parse_command_to_ir("install.bat --arg")
    assert bat_ir.verb_class == VerbClass.EXECUTES_CODE
    assert bat_ir.is_safe_to_probe is False
    assert "BatBadBut" in (bat_ir.rejection_reason or "")

    cmd_ir = parse_command_to_ir("build.cmd")
    assert cmd_ir.verb_class == VerbClass.EXECUTES_CODE
    assert cmd_ir.is_safe_to_probe is False
    assert "BatBadBut" in (cmd_ir.rejection_reason or "")


def test_command_ir_default_deny():
    """Verify unknown binary names are rejected under default-deny policy."""
    unknown_ir = parse_command_to_ir("my_custom_mystery_tool --run")
    assert unknown_ir.verb_class == VerbClass.DESTRUCTIVE
    assert unknown_ir.is_safe_to_probe is False
    assert "default-deny" in (unknown_ir.rejection_reason or "")
