"""
test_direct_loop_exit_contract.py - Unit tests for Living Disk State Exit Contract
and Verification Ladder integration in direct_loop.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock
from aja.orchestration.plan_state import (
    init_plan,
    update_task_status,
    TaskStatus,
)
from aja.orchestration.verification_runner import (
    VerificationResult,
)
from aja.orchestration.direct_loop import run_direct_loop
from aja.cli.registry import CommandRegistry


class MockGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.last_system_prompt = None

    async def chat(self, model=None, prompt=None, system=None, tools=None):
        self.call_count += 1
        self.last_system_prompt = system
        if self.responses:
            return self.responses.pop(0)
        return "Finished."


class MockRegistry:
    def get_schemas(self, interactive=True):
        return []


class MockExecutor:
    async def dispatch_tool_calls(self, tool_calls, trace_id, dry_run=False):
        return []


def test_plan_exit_contract_rejects_incomplete_plan(tmp_path: Path):
    """
    Test that direct loop rejects completion if PLAN.md has pending tasks,
    injects feedback, and when tasks are completed, allows exit.
    """
    plan_file = tmp_path / "PLAN.md"
    init_plan(
        "Modernize harness",
        ["Implement verification ladder", "Implement plan state"],
        path=plan_file,
    )

    async def _run():
        class PlanCompletingGateway(MockGateway):
            async def chat(self, model=None, prompt=None, system=None, tools=None):
                if self.call_count == 1:
                    # After turn 1 was rejected, mark tasks completed on turn 2
                    update_task_status(plan_file, 1, TaskStatus.DONE)
                    update_task_status(plan_file, 2, TaskStatus.DONE)
                return await super().chat(model=model, prompt=prompt, system=system, tools=tools)

        gateway = PlanCompletingGateway([
            "I'm all done now!",  # Attempt 1 -> plan incomplete, rejected
            "Now all tasks are done for real.",  # Attempt 2 -> tasks completed, accepted
        ])

        outcome = await run_direct_loop(
            "Complete the plan",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            plan_path=plan_file,
            max_turns=5,
            max_verification_retries=3,
        )

        assert outcome is not None
        assert outcome["status"] == "completed"
        assert outcome["turns"] == 2
        # Verify that Living Disk State was injected into the turn system prompt
        assert "[Living Disk State: PLAN.md]" in (gateway.last_system_prompt or "")

    asyncio.run(_run())


def test_plan_exit_contract_exhaustion(tmp_path: Path):
    """
    Test that if the model keeps attempting to exit without finishing tasks,
    it exits with status: incomplete, reason: plan_incomplete.
    """
    plan_file = tmp_path / "PLAN.md"
    init_plan(
        "Unfinished goal",
        ["Never gonna finish this"],
        path=plan_file,
    )

    async def _run():
        gateway = MockGateway([
            "I'm done 1",
            "I'm done 2",
            "I'm done 3",
            "I'm done 4",
        ])

        outcome = await run_direct_loop(
            "Finish unfinished goal",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            plan_path=plan_file,
            max_turns=10,
            max_verification_retries=2,
        )

        assert outcome is not None
        assert outcome["status"] == "incomplete"
        assert outcome["reason"] == "plan_incomplete"

    asyncio.run(_run())


def test_verification_ladder_gate_integration(tmp_path: Path):
    """
    Test that ladder_verify=True runs the verification ladder and handles
    failures and self-healing.
    """
    async def _run():
        gateway = MockGateway([
            "Done first pass.",
            "Done second pass with fix.",
        ])

        ladder_calls = 0

        async def mock_run_ladder(**kwargs):
            nonlocal ladder_calls
            ladder_calls += 1
            if ladder_calls == 1:
                return VerificationResult(
                    passed=False,
                    check_type="ladder",
                    tier=1,
                    summary="Tier 1 (Syntax) failed: Syntax error in test.py",
                    details="Syntax error in test.py",
                    exit_code=1,
                )
            return VerificationResult(
                passed=True,
                check_type="ladder",
                tier=3,
                summary="All tiers passed",
                exit_code=0,
            )

        with patch("aja.orchestration.verification_runner.run_verification_ladder", side_effect=mock_run_ladder):
            outcome = await run_direct_loop(
                "Run with verification ladder",
                gateway=gateway,
                tools_registry=MockRegistry(),
                executor=MockExecutor(),
                ladder_verify=True,
                max_turns=5,
                max_verification_retries=3,
            )

        assert outcome is not None
        assert outcome["status"] == "completed"
        assert outcome["verified"] is True
        assert ladder_calls == 2

    asyncio.run(_run())


def test_cli_registry_flags_forwarding():
    """
    Test that CommandRegistry forwards --swarm and --no-plan to cmd_run without
    polluting the mission objective string.
    """
    registry = CommandRegistry()

    with patch("aja.cli.commands.run.cmd_run") as mock_cmd_run:
        registry.dispatch(["run", "build", "a", "robot", "--swarm", "--no-plan", "--dry-run"])

        mock_cmd_run.assert_called_once_with(
            "build a robot",
            background=False,
            dry_run=True,
            swarm=True,
            no_plan=True,
            model=None,
        )
