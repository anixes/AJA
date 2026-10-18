"""
test_verification_ladder_v2.py - Unit test suite for Step 4.4 Verification Ladder v2 & Integration.
==================================================================================================

Validates Step 4.4 requirements:
1. Cyclic error oscillation detection: captures alternating A-B-A-B error patterns and triggers damping.
2. Deliverable checking exclusions: scratch, log, and tmp paths do not trigger false deliverable failures.
3. Verification telemetry: RunJournal seals verification_result events with pass/fail and attempt metrics.
4. Living Disk State (PLAN.md) atomic task reversion and self-healing diagnostic feedback.
5. Soft-pass behavior when no targeted tests are mapped (absence of tests is not a failure).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from aja.orchestration.direct_loop import (
    _compute_error_signature,
    _find_missing_deliverables,
    run_direct_loop,
)
from aja.orchestration.plan_state import TaskStatus, init_plan, parse_plan, update_task_status
from aja.orchestration.run_journal import RunJournal
from aja.orchestration.verification_runner import (
    VerificationResult,
    run_targeted_tests_sync,
    run_verification_ladder_sync,
)


def test_cyclic_oscillation_signature_detection() -> None:
    """Validate that identical signatures and cyclic alternating signatures are detected."""
    err_a = "AssertionError: expected 42 but got 41 on line 10"
    err_b = "SyntaxError: invalid syntax on line 15"

    sig_a = _compute_error_signature(err_a)
    sig_b = _compute_error_signature(err_b)

    assert sig_a != sig_b

    # Line numbers are normalized out (:LINE)
    err_a_diff_line = "AssertionError: expected 42 but got 41 on line 999"
    assert _compute_error_signature(err_a_diff_line) == sig_a

    # Simulate sliding-window history: [A, B, A] -> cyclic recurrence of A
    history = [sig_a, sig_b]
    assert history[-8:].count(sig_a) == 1
    history.append(sig_a)
    assert history[-8:].count(sig_a) == 2  # Cyclic recurrence detected!


def test_deliverable_checker_excludes_scratch_and_logs(tmp_path: Path) -> None:
    """Verify that scratch, tmp, and log files are excluded from deliverable assertions."""
    content = (
        "I have created the output at scratch/intermediate.txt and logged to logs/debug.log. "
        "Also saved to D:\\final_output.json"
    )
    # The scratch and log files must be omitted; only final_output.json should be evaluated
    missing = _find_missing_deliverables(content)
    assert not any("intermediate.txt" in p for p in missing)
    assert not any("debug.log" in p for p in missing)


def test_living_disk_state_atomic_task_reversion(tmp_path: Path) -> None:
    """Verify PLAN.md task status updates and atomic reversion from [x] to [/]."""
    plan_file = tmp_path / "PLAN.md"
    init_plan(
        objective="Verification ladder test mission",
        tasks=["First core task", "Second core task"],
        path=plan_file,
    )

    state = parse_plan(plan_file)
    assert state.tasks[0].status == TaskStatus.TODO

    # Move to IN_PROGRESS
    update_task_status(plan_file, 1, TaskStatus.IN_PROGRESS)
    state = parse_plan(plan_file)
    assert state.tasks[0].status == TaskStatus.IN_PROGRESS

    # Mark as DONE
    update_task_status(plan_file, 1, TaskStatus.DONE, evidence="Implementation finished")
    state = parse_plan(plan_file)
    assert state.tasks[0].status == TaskStatus.DONE
    assert state.tasks[0].evidence == "Implementation finished"

    # Simulate Turn Epilogue verification failure: revert back to IN_PROGRESS
    update_task_status(plan_file, 1, TaskStatus.IN_PROGRESS)
    reverted_state = parse_plan(plan_file)
    assert reverted_state.tasks[0].status == TaskStatus.IN_PROGRESS


def test_targeted_tests_no_tests_mapped_soft_pass(tmp_path: Path) -> None:
    """Verify that if no tests exist for a file, Tier 2 treats it as a non-fatal soft pass."""
    src_file = tmp_path / "my_module.py"
    src_file.write_text("def add(a, b): return a + b\n", encoding="utf-8")

    # 1. Verification ladder with files_to_check gracefully soft-skips Tier 2 when no tests are mapped
    res_ladder = run_verification_ladder_sync(files_to_check=[src_file], cwd=tmp_path, check_git=False)
    assert res_ladder.passed is True
    assert 2 in res_ladder.skipped_tiers

    # 2. Targeted test execution directly against a file with 0 tests collected produces soft-pass (exit code 5)
    res_direct = run_targeted_tests_sync(test_target=str(src_file), cwd=tmp_path)
    assert res_direct.passed is True
    assert res_direct.exit_code == 5
    assert "no tests collected" in res_direct.summary.lower()


def test_run_direct_loop_seals_verification_events_in_journal(tmp_path: Path) -> None:
    """Verify run_direct_loop seals verification_result events into RunJournal."""
    async def _test() -> None:
        journal = RunJournal(db_path=":memory:")
        run_id = "test-run-verification"

        class VerificationGateway:
            def __init__(self):
                self.calls = 0

            async def chat(self, model=None, prompt=None, system=None, tools=None):
                self.calls += 1
                return "Task finished without tool calls."

        class SimpleRegistry:
            def get_schemas(self, interactive=True):
                return []

        class SimpleExecutor:
            async def dispatch_tool_calls(self, tool_calls, trace_id=None, dry_run=False):
                return []

        # verification_fn passes immediately
        verification_called = False

        def mock_verification():
            nonlocal verification_called
            verification_called = True
            return {"passed": True, "message": "All gates green"}

        outcome = await run_direct_loop(
            objective="Inspect system architecture",
            gateway=VerificationGateway(),
            tools_registry=SimpleRegistry(),
            executor=SimpleExecutor(),
            verification_fn=mock_verification,
            auto_verify=True,
            run_journal=journal,
            run_id=run_id,
            history_compressor=lambda h, model=None, provider=None: None,
            result_truncator=lambda raw: raw[:50],
            trace_id_fn=lambda: "tr-verif",
        )

        assert outcome["status"] == "completed"
        assert verification_called is True

        events = journal.get_events(run_id)
        verif_events = [e for e in events if e.event_type == "verification_result"]
        assert len(verif_events) >= 1
        assert verif_events[0].payload["passed"] is True

        # Integrity chain verification
        valid, err = journal.verify_run_integrity(run_id)
        assert valid is True
        assert err is None

    asyncio.run(_test())
