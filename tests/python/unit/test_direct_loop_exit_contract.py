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


from tests.fixtures.mocks import (
    BaseMockGateway as MockGateway,
    BaseMockRegistry as MockRegistry,
    BaseMockExecutor as MockExecutor,
)


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
        # Verify that Living Disk State was injected as an ephemeral trailing user anchor,
        # leaving system_prompt clean and static to protect provider prefix caching.
        assert any("[Living Disk State — Current Snapshot]" in str(m.get("content")) for m in gateway.last_prompt)
        assert "[Living Disk State: PLAN.md]" not in (gateway.last_system_prompt or "")

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


def test_incremental_task_verification_gate(tmp_path: Path):
    """
    Test P0.3: Incremental per-task verification demotes broken completed tasks,
    injects feedback, and allows progression once checks pass.
    """
    plan_file = tmp_path / "PLAN.md"
    init_plan("Test Incremental", ["Step One", "Step Two"], path=plan_file)

    async def _run():
        turn = 0
        ladder_calls = 0

        class IncrementalGateway(MockGateway):
            async def chat(self, model=None, prompt=None, system=None, tools=None):
                nonlocal turn
                turn += 1
                if turn == 1:
                    # Mark task 1 complete, but verification will fail
                    update_task_status(plan_file, 1, TaskStatus.DONE)
                    return "Task 1 finished."
                elif turn == 2:
                    # Check that task 1 was reverted to IN_PROGRESS and diagnostic feedback was injected
                    assert any("[Incremental Task Verification Failed" in m.get("content", "") for m in prompt)
                    # Model repairs it and marks task 1 DONE again
                    update_task_status(plan_file, 1, TaskStatus.DONE)
                    return "Repaired task 1."
                elif turn == 3:
                    # Mark task 2 DONE
                    update_task_status(plan_file, 2, TaskStatus.DONE)
                    return "Task 2 finished."
                return "All done."

        async def mock_run_ladder(*args, **kwargs):
            nonlocal ladder_calls
            ladder_calls += 1
            if ladder_calls == 1:
                # First incremental check fails
                return VerificationResult(
                    passed=False,
                    check_type="ladder",
                    tier=1,
                    summary="SyntaxError in foo.py",
                    details="foo.py:10: Invalid syntax",
                    exit_code=1,
                )
            # Subsequent checks pass
            return VerificationResult(
                passed=True,
                check_type="ladder",
                tier=1,
                summary="Checks passed",
                exit_code=0,
            )

        gateway = IncrementalGateway([])

        with patch("aja.orchestration.verification_runner.run_verification_ladder", side_effect=mock_run_ladder):
            outcome = await run_direct_loop(
                "Test Incremental Loop",
                gateway=gateway,
                tools_registry=MockRegistry(),
                executor=MockExecutor(),
                ladder_verify=True,
                plan_path=plan_file,
                use_plan=True,
                max_turns=5,
            )

        assert outcome is not None
        assert outcome["status"] == "completed"
        # Task 1 failed once, passed on retry, Task 2 passed, plus final exit contract
        assert ladder_calls >= 2

    asyncio.run(_run())


def test_ephemeral_plan_anchor_preserves_static_system_and_history_invariance(tmp_path: Path):
    """
    Verify P1.2 architecture:
    1. system_prompt remains 100% static across multi-turn execution (protecting prefix caching).
    2. Ephemeral plan anchor is injected at the tail of call_prompt.
    3. Caller's session_history is NOT polluted with trailing duplicate anchors.
    """
    plan_file = tmp_path / "PLAN.md"
    init_plan(
        "Static System Test",
        ["Task A", "Task B"],
        path=plan_file,
    )

    async def _run():
        class MultiTurnGateway(MockGateway):
            async def chat(self, model=None, prompt=None, system=None, tools=None):
                if self.call_count == 1:
                    update_task_status(plan_file, 1, TaskStatus.DONE)
                elif self.call_count == 2:
                    update_task_status(plan_file, 2, TaskStatus.DONE)
                return await super().chat(model=model, prompt=prompt, system=system, tools=tools)

        gateway = MultiTurnGateway([
            "Finished step 1",
            "Finished step 2",
            "Finished all steps",
        ])

        history = [{"role": "user", "content": "Start work"}]
        static_system = "You are a specialized test agent."

        outcome = await run_direct_loop(
            "Execute mission",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            system_prompt=static_system,
            session_history=history,
            plan_path=plan_file,
            max_turns=5,
        )

        assert outcome is not None
        assert outcome["status"] == "completed"
        # 1. System prompt was 100% static on every call
        assert len(gateway.system_prompts_seen) >= 2
        for s in gateway.system_prompts_seen:
            assert s == static_system

        # 2. Ephemeral anchor appeared in call_prompt on each turn
        assert any("[Living Disk State — Current Snapshot]" in str(m.get("content")) for m in gateway.last_prompt)

        # 3. Caller's persistent history does NOT have trailing plan anchors
        for msg in history:
            assert "[Living Disk State — Current Snapshot]" not in str(msg.get("content"))

    asyncio.run(_run())


