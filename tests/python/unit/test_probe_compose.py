"""
test_probe_compose.py - Unit tests for Propose-Probe-Compose Tier 0 probing sandbox.
===================================================================================
"""

import sys
from pathlib import Path
import pytest

from aja.orchestration.verification_runner import (
    ProbeSandbox,
    probe_python_snippet,
    probe_shell_command,
    VerificationResult,
)


def test_probe_python_valid_snippet():
    code = """
def add(a: int, b: int) -> int:
    return a + b

assert add(2, 3) == 5
"""
    res = probe_python_snippet(code)
    assert res.passed is True
    assert res.exit_code == 0
    assert res.check_type == "probe_python"
    assert "passed" in res.summary.lower()


def test_probe_python_syntax_error():
    bad_code = """
def broken(
    print("missing closing paren"
"""
    res = probe_python_snippet(bad_code)
    assert res.passed is False
    assert res.exit_code == 1
    assert res.error_type == "syntax_error"
    assert "syntax error" in res.summary.lower()
    assert "Line" in res.details


def test_probe_python_syntax_only():
    code = "import nonexistent_module_12345"
    # In syntax only mode, nonexistent imports are valid syntax
    res = probe_python_snippet(code, check_syntax_only=True)
    assert res.passed is True
    assert res.exit_code == 0


def test_probe_shell_blocks_dangerous_command():
    dangerous_cmds = [
        "rm -rf /",
        "rm -rf ~",
        "mkfs /dev/sda1",
        ":(){ :|:& };:",
        "format C:",
        "del /s /q c:\\windows",
    ]
    for cmd in dangerous_cmds:
        res = probe_shell_command(cmd, dry_run=True)
        assert res.passed is False
        assert res.exit_code == 126
        assert res.error_type == "safety_violation"
        assert "security probe failed" in res.summary.lower()


def test_probe_shell_safe_command_dry_run():
    res = probe_shell_command("echo 'Hello World'", dry_run=True)
    assert res.passed is True
    assert res.exit_code == 0
    assert res.command == "echo 'Hello World'"


def test_probe_shell_isolated_execution(tmp_path: Path):
    cmd = f'"{sys.executable}" -c "print(1 + 2)"'
    res = probe_shell_command(cmd, cwd=tmp_path, dry_run=False)
    assert res.passed is True
    assert res.exit_code == 0
    assert "3" in res.details


def test_probe_shell_failing_command(tmp_path: Path):
    cmd = f'"{sys.executable}" -c "import sys; sys.exit(42)"'
    res = probe_shell_command(cmd, cwd=tmp_path, dry_run=False)
    assert res.passed is False
    assert res.exit_code == 42
