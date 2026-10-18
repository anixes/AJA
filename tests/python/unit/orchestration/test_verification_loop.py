"""
test_verification_loop.py - Unit tests for autonomous verification gate in direct_loop.
=====================================================================================
"""

import asyncio
from pathlib import Path
from aja.orchestration.verification_runner import (
    VerificationResult,
    verify_python_syntax,
    run_command_verifier,
)
from aja.orchestration.direct_loop import run_direct_loop


def test_verify_python_syntax_valid(tmp_path: Path):
    valid_file = tmp_path / "valid.py"
    valid_file.write_text("def hello():\n    return 42\n", encoding="utf-8")

    res = verify_python_syntax([valid_file])
    assert res.passed is True
    assert res.exit_code == 0
    assert "passed" in res.summary


def test_verify_python_syntax_invalid(tmp_path: Path):
    invalid_file = tmp_path / "invalid.py"
    invalid_file.write_text("def broken(\n    return 42\n", encoding="utf-8")

    res = verify_python_syntax([invalid_file])
    assert res.passed is False
    assert res.exit_code == 1
    assert "syntax errors" in res.summary
    assert "invalid.py" in (res.details or "")

    feedback = res.to_feedback_prompt()
    assert "[Autonomous Verification Failure: python_syntax]" in feedback


import sys

def test_run_command_verifier_success():
    async def _run():
        cmd = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
        res = await run_command_verifier(cmd)
        assert res.passed is True
        assert res.exit_code == 0

    asyncio.run(_run())


def test_run_command_verifier_failure():
    async def _run():
        cmd = f'"{sys.executable}" -c "import sys; sys.stderr.write(\'fatal bug\'); sys.exit(1)"'
        res = await run_command_verifier(cmd)
        assert res.passed is False
        assert res.exit_code == 1
        assert "fatal bug" in (res.details or "")
        feedback = res.to_feedback_prompt()
        assert "Exit code: 1" in feedback

    asyncio.run(_run())


class MockGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.provider = "mock"

    async def chat(self, model=None, prompt=None, system=None, tools=None):
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        return "Finished."


class MockRegistry:
    def get_schemas(self, interactive=True):
        return []


class MockExecutor:
    async def dispatch_tool_calls(self, tool_calls, trace_id, dry_run=False):
        return []


def test_direct_loop_self_healing_verification():
    async def _run():
        # Model attempts to finish twice:
        # 1st attempt: verification fails -> synthetic feedback injected
        # 2nd attempt: verification passes -> loop completes with verified=True
        gateway = MockGateway([
            "I have completed the task, Sir.",
            "I have fixed the issue and now it is clean, Sir.",
        ])

        check_call_count = 0

        def mock_verification():
            nonlocal check_call_count
            check_call_count += 1
            if check_call_count == 1:
                return VerificationResult(
                    passed=False,
                    check_type="test_suite",
                    summary="AssertionError in test_logic",
                    details="Expected 42, got 0",
                    exit_code=1,
                )
            return VerificationResult(
                passed=True,
                check_type="test_suite",
                summary="All tests passed.",
                exit_code=0,
            )

        outcome = await run_direct_loop(
            "Fix bug in math logic",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            max_turns=5,
            verification_fn=mock_verification,
            max_verification_retries=3,
        )

        assert outcome is not None
        assert outcome["status"] == "completed"
        assert outcome["verified"] is True
        assert outcome["turns"] == 2
        assert check_call_count == 2

    asyncio.run(_run())


def test_direct_loop_verification_exhaustion():
    async def _run():
        # Verification always fails: loop should exit with incomplete / verification_failed
        gateway = MockGateway([
            "Attempt 1 done.",
            "Attempt 2 done.",
            "Attempt 3 done.",
            "Attempt 4 done.",
        ])

        def failing_verification():
            return VerificationResult(
                passed=False,
                check_type="linter",
                summary="Style violation",
                exit_code=1,
            )

        outcome = await run_direct_loop(
            "Format code",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            max_turns=10,
            verification_fn=failing_verification,
            max_verification_retries=2,
        )

        assert outcome is not None
        assert outcome["status"] == "incomplete"
        assert outcome["reason"] == "verification_failed"

    asyncio.run(_run())
