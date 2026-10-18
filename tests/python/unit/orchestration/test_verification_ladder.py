"""
test_verification_ladder.py - Comprehensive tests for the Deterministic Verification Ladder.
=============================================================================================
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aja.orchestration.verification_runner import (
    VerificationResult,
    run_command_verifier,
    run_targeted_tests,
    run_targeted_tests_sync,
    run_verification_ladder,
    run_verification_ladder_sync,
    smart_truncate_output,
    verify_git_diff,
    verify_python_syntax,
    verify_syntax_in_workspace,
)


def test_smart_truncate_output_edge_cases():
    # 1. Empty string
    assert smart_truncate_output("") == ""

    # 2. Text shorter than head + tail limits
    short = "line 1\nline 2\nline 3"
    assert smart_truncate_output(short, max_head_lines=5, max_tail_lines=5) == short

    # 3. Head + tail truncation with middle marker
    many_lines = "\n".join(f"line {i}" for i in range(100))
    truncated = smart_truncate_output(many_lines, max_head_lines=5, max_tail_lines=5)
    assert "line 0" in truncated
    assert "line 4" in truncated
    assert "[... truncated 90 lines ...]" in truncated
    assert "line 95" in truncated
    assert "line 99" in truncated
    assert "line 50" not in truncated

    # 4. Enforce max_bytes without breaking unicode characters
    unicode_text = "✨" * 500  # 4 bytes per emoji = 2000 bytes
    capped = smart_truncate_output(unicode_text, max_bytes=100)
    assert len(capped.encode("utf-8")) <= 150  # includes marker
    # Must be valid UTF-8 string
    assert isinstance(capped, str)


def test_verify_python_syntax_granularity(tmp_path: Path):
    # Create multiple files with syntax errors
    f1 = tmp_path / "broken1.py"
    f1.write_text("def broken1(\n", encoding="utf-8")
    f2 = tmp_path / "broken2.py"
    f2.write_text("def broken2(\n", encoding="utf-8")
    f3 = tmp_path / "clean.py"
    f3.write_text("def clean(): return 1\n", encoding="utf-8")

    res = verify_python_syntax([f1, f2, f3])
    assert res.passed is False
    assert res.tier == 1
    assert res.exit_code == 1
    assert "2 file(s) contain syntax errors" in res.summary
    assert "broken1.py" in res.details
    assert "broken2.py" in res.details
    assert res.duration_ms >= 0


def test_verify_syntax_in_workspace(tmp_path: Path):
    # Set up simulated workspace
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("x = 10\n", encoding="utf-8")

    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "bad_syntax.py").write_text("def broken(\n", encoding="utf-8")

    # Should ignore .venv
    res = verify_syntax_in_workspace(root=tmp_path)
    assert res.passed is True
    assert "All 1 Python file(s) passed" in res.summary


def test_verify_syntax_in_workspace_partial_disclosure(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"test_{i}.py").write_text("val = 1\n", encoding="utf-8")

    res = verify_syntax_in_workspace(root=tmp_path, max_files=3)
    assert res.passed is True
    assert "partial coverage" in res.summary
    assert "Checked first 3 of 5 Python files" in res.summary


def test_run_targeted_tests_sync_and_async():
    # 1. Sync success
    cmd = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
    res_sync = run_targeted_tests_sync(test_target=cmd)
    assert res_sync.passed is True
    assert res_sync.tier == 2
    assert res_sync.exit_code == 0

    # 2. Async success
    async def _test_async():
        res_async = await run_targeted_tests(test_target=cmd)
        assert res_async.passed is True
        assert res_async.tier == 2
        assert res_async.exit_code == 0

    asyncio.run(_test_async())


def test_run_targeted_tests_timeout():
    # Long sleeping script
    cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    res = run_targeted_tests_sync(test_target=cmd, timeout=1)
    assert res.passed is False
    assert res.tier == 2
    assert res.exit_code == -1
    assert "timed out after 1s" in res.summary


def test_verify_git_diff_untracked_and_wipeout(tmp_path: Path):
    # Initialize a temporary git repository
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True, check=True)

    # 1. Clean repo with no changes -> empty diff failure if allow_empty=False
    init_file = tmp_path / "initial.txt"
    init_file.write_text("initial content\n", encoding="utf-8")
    subprocess.run(["git", "add", "initial.txt"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), capture_output=True, check=True)

    res_empty = verify_git_diff(cwd=tmp_path, allow_empty=False)
    assert res_empty.passed is False
    assert "no modifications or new files detected" in res_empty.summary

    # 2. New untracked file -> should pass empty-diff guard
    new_file = tmp_path / "new_module.py"
    new_file.write_text("print('hello')\n", encoding="utf-8")
    res_untracked = verify_git_diff(cwd=tmp_path, allow_empty=False)
    assert res_untracked.passed is True

    # Commit the new file
    subprocess.run(["git", "add", "new_module.py"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "add module"], cwd=str(tmp_path), capture_output=True, check=True)

    # 3. Simulate large file deletion (wipeout heuristic)
    large_file = tmp_path / "large.txt"
    large_file.write_text("line\n" * 200, encoding="utf-8")
    subprocess.run(["git", "add", "large.txt"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "add large"], cwd=str(tmp_path), capture_output=True, check=True)

    # Wipe large.txt
    large_file.write_text("replacement\n", encoding="utf-8")  # +1, -199
    res_wipeout = verify_git_diff(cwd=tmp_path, max_deletions=100)
    assert res_wipeout.passed is False
    assert "Suspicious massive deletion" in res_wipeout.summary
    assert "large.txt" in res_wipeout.details


def test_verification_ladder_short_circuit_and_skipped_tiers(tmp_path: Path):
    broken_file = tmp_path / "broken.py"
    broken_file.write_text("def broken(\n", encoding="utf-8")

    # Tier 1 fails -> short-circuit Tier 2 and Tier 3
    res = run_verification_ladder_sync(
        files_to_check=[broken_file],
        test_target="echo test",
        check_git=True,
        cwd=tmp_path,
    )
    assert res.passed is False
    assert res.check_type == "ladder"
    assert res.tier == 1
    assert res.skipped_tiers == [2, 3]
    assert "Tier 1 (Syntax) failed" in res.summary

    prompt = res.to_feedback_prompt()
    assert "[Autonomous Verification Failure: ladder]" in prompt
    assert "Tier: 1" in prompt
    assert "Skipped Tiers: [2, 3]" in prompt


def test_verification_ladder_all_pass(tmp_path: Path):
    clean_file = tmp_path / "clean.py"
    clean_file.write_text("def ok(): return 1\n", encoding="utf-8")

    cmd = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
    res = run_verification_ladder_sync(
        files_to_check=[clean_file],
        test_target=cmd,
        check_git=False,
        cwd=tmp_path,
    )
    assert res.passed is True
    assert res.check_type == "ladder"
    assert res.tier is None
    assert "All active verification ladder tiers passed" in res.summary
