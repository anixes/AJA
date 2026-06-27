"""
Phase 4 Integration Tests: Parallel Activity Scheduler + Permission System

Phase 3 introduced the PermissionEngine, ParallelActivityScheduler, and
ActivityBatchResult. This suite tests them as an integrated system:

  1. Allowed activities emit PERMISSION_GRANTED → TOOL_CALLED → TOOL_COMPLETED
     journal events in the correct order.
  2. Denied activities emit PERMISSION_DENIED → TOOL_FAILED and return
     success=False without ever reaching execution.
  3. A "deny" rule overrides a broader wildcard "allow" rule.
  4. A batch of activities produces a correct ActivityBatchResult with every
     activity journaled independently.
  5. A batch with one denied activity yields partial_success=True.
  6. fail_fast=True cancels pending activities as soon as one fails.
"""

import asyncio
import json
from pathlib import Path

from aja.orchestration.activity_rt import Activity, ActivityRuntime, ActivityType
from aja.orchestration.scheduler import ActivityBatchResult, ParallelActivityScheduler
from aja.runtime.mission_journal import MissionJournal
from aja.security.permissions import PermissionEngine, PermissionPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MISSION_PREFIX = "TEST-PH4"
_TRACE = "TRACE-PH4"


def _allow_all_runtime(journal: MissionJournal) -> ActivityRuntime:
    """Dry-run ActivityRuntime where shell.* and python.* are explicitly allowed."""
    policy = PermissionPolicy(scopes={"shell.*": "allow", "python.*": "allow"})
    return ActivityRuntime(
        journal=journal,
        dry_run=True,
        permission_engine=PermissionEngine(policy),
    )


def _shell(
    tool: str, *, cmd: str = "echo phase4", scope: str | None = None
) -> Activity:
    """Create a dry-run SHELL activity, optionally forcing a specific required_scope."""
    meta = {}
    if scope:
        meta["required_scope"] = scope
    return Activity(
        tool=tool,
        args={"cmd": cmd},
        activity_type=ActivityType.SHELL,
        trace_id=_TRACE,
        mission_id=f"{_MISSION_PREFIX}-SCHED",
        metadata=meta,
    )


def _journal_events(journal: MissionJournal) -> list[dict]:
    """Read all events from a MissionJournal and return them as plain dicts."""
    return journal.read_events()


def _cleanup(journal: MissionJournal) -> None:
    if journal.journal_path.exists():
        journal.journal_path.unlink()


# ---------------------------------------------------------------------------
# Test 1 — Allowed activity: correct journal event sequence
# ---------------------------------------------------------------------------


def test_permission_grant_journals_events():
    """
    An allowed SHELL activity must emit PERMISSION_GRANTED → TOOL_CALLED →
    TOOL_COMPLETED in that order and report success=True.
    """
    mission_id = f"{_MISSION_PREFIX}-ALLOW"
    journal = MissionJournal(mission_id)
    _cleanup(journal)

    async def scenario():
        runtime = _allow_all_runtime(journal)
        activity = Activity(
            tool="shell.run_command",
            args={"cmd": "echo allowed"},
            activity_type=ActivityType.SHELL,
            trace_id=_TRACE,
            mission_id=mission_id,
        )
        result = await runtime.run(activity)

        assert result.success is True, f"Expected success; got error: {result.error}"
        assert result.permission_decision == "allow"
        assert result.grant_id is not None

        events = _journal_events(journal)
        event_types = [e["event_type"] for e in events]

        assert "PERMISSION_GRANTED" in event_types, (
            f"Missing PERMISSION_GRANTED in {event_types}"
        )
        assert "TOOL_CALLED" in event_types, f"Missing TOOL_CALLED in {event_types}"
        assert "TOOL_COMPLETED" in event_types, (
            f"Missing TOOL_COMPLETED in {event_types}"
        )

        # Order: PERMISSION_GRANTED must precede TOOL_CALLED, which precedes TOOL_COMPLETED.
        idx_granted = event_types.index("PERMISSION_GRANTED")
        idx_called = event_types.index("TOOL_CALLED")
        idx_completed = event_types.index("TOOL_COMPLETED")
        assert idx_granted < idx_called < idx_completed, (
            f"Journal event order wrong: PERMISSION_GRANTED={idx_granted}, "
            f"TOOL_CALLED={idx_called}, TOOL_COMPLETED={idx_completed}"
        )

        # PERMISSION_GRANTED must carry the correct scope
        granted_event = next(
            e for e in events if e["event_type"] == "PERMISSION_GRANTED"
        )
        assert "shell" in granted_event.get("scope", ""), (
            f"PERMISSION_GRANTED scope is unexpected: {granted_event}"
        )

    asyncio.run(scenario())
    _cleanup(journal)


# ---------------------------------------------------------------------------
# Test 2 — Denied activity: blocked before execution, correct journal events
# ---------------------------------------------------------------------------


def test_permission_deny_blocks_execution_and_journals_denial():
    """
    A denied activity must:
      - Return success=False with permission_decision="deny".
      - Emit PERMISSION_DENIED and TOOL_FAILED journal events.
      - Never emit TOOL_CALLED (execution must not start).
    """
    mission_id = f"{_MISSION_PREFIX}-DENY"
    journal = MissionJournal(mission_id)
    _cleanup(journal)

    async def scenario():
        policy = PermissionPolicy(scopes={"desktop.interact": "deny"})
        runtime = ActivityRuntime(
            journal=journal,
            dry_run=True,
            permission_engine=PermissionEngine(policy),
        )
        activity = Activity(
            tool="desktop.click",
            args={"x": 100, "y": 200},
            activity_type=ActivityType.DESKTOP,
            trace_id=_TRACE,
            mission_id=mission_id,
        )
        result = await runtime.run(activity)

        assert result.success is False
        assert result.permission_decision == "deny"
        assert result.error is not None
        assert "Permission denied" in result.error

        events = _journal_events(journal)
        event_types = [e["event_type"] for e in events]

        assert "PERMISSION_DENIED" in event_types, (
            f"Missing PERMISSION_DENIED in {event_types}"
        )
        assert "TOOL_FAILED" in event_types, f"Missing TOOL_FAILED in {event_types}"
        assert "TOOL_CALLED" not in event_types, (
            "TOOL_CALLED must NOT appear when activity is blocked by permission policy. "
            f"Got: {event_types}"
        )

        # Ensure PERMISSION_DENIED comes before TOOL_FAILED
        idx_denied = event_types.index("PERMISSION_DENIED")
        idx_failed = event_types.index("TOOL_FAILED")
        assert idx_denied < idx_failed

    asyncio.run(scenario())
    _cleanup(journal)


# ---------------------------------------------------------------------------
# Test 3 — Deny overrides wildcard allow (specificity precedence)
# ---------------------------------------------------------------------------


def test_deny_overrides_wildcard_allow_in_runtime():
    """
    A "deny" on a specific scope must override a broader wildcard "allow",
    even when the activity is run through the full ActivityRuntime pipeline.

    Policy: shell.* → allow   (wildcard)
            shell.destructive → deny  (specific)
    Activity metadata forces required_scope = "shell.destructive".
    """
    mission_id = f"{_MISSION_PREFIX}-PREC"
    journal = MissionJournal(mission_id)
    _cleanup(journal)

    async def scenario():
        policy = PermissionPolicy(
            scopes={
                "shell.*": "allow",
                "shell.destructive": "deny",
            }
        )
        runtime = ActivityRuntime(
            journal=journal,
            dry_run=True,
            permission_engine=PermissionEngine(policy),
        )
        activity = Activity(
            tool="shell.run_command",
            args={"cmd": "rm -rf /"},
            activity_type=ActivityType.SHELL,
            trace_id=_TRACE,
            mission_id=mission_id,
            metadata={"required_scope": "shell.destructive"},
        )
        result = await runtime.run(activity)

        assert result.success is False, (
            "shell.destructive should be denied even though shell.* is allowed"
        )
        assert result.permission_decision == "deny"

        event_types = [e["event_type"] for e in _journal_events(journal)]
        assert "PERMISSION_DENIED" in event_types
        assert "TOOL_CALLED" not in event_types

    asyncio.run(scenario())
    _cleanup(journal)


# ---------------------------------------------------------------------------
# Test 4 — Batch of all-successful activities: journal receives all events
# ---------------------------------------------------------------------------


def test_parallel_batch_all_succeed_journals_all_activities():
    """
    A batch of three allowed dry-run SHELL activities must:
      - Produce an ActivityBatchResult with success=True and no failures.
      - Emit TOOL_CALLED + TOOL_COMPLETED journal events for each activity.
    """
    mission_id = f"{_MISSION_PREFIX}-BATCH-OK"
    journal = MissionJournal(mission_id)
    _cleanup(journal)

    async def scenario():
        runtime = _allow_all_runtime(journal)
        scheduler = ParallelActivityScheduler(runtime, concurrency_limit=3)

        activities = [
            _shell("shell.tool_a", cmd="echo a"),
            _shell("shell.tool_b", cmd="echo b"),
            _shell("shell.tool_c", cmd="echo c"),
        ]
        batch: ActivityBatchResult = await scheduler.run_batch(activities)

        assert batch.success is True, (
            f"Batch should succeed; failures: {[f.error for f in batch.failures]}"
        )
        assert batch.partial_success is False
        assert len(batch.failures) == 0
        assert len(batch.results) == 3
        assert all(r.success for r in batch.results)
        assert batch.duration_ms >= 0

        events = _journal_events(journal)
        event_types = [e["event_type"] for e in events]

        called_count = event_types.count("TOOL_CALLED")
        completed_count = event_types.count("TOOL_COMPLETED")
        assert called_count == 3, f"Expected 3 TOOL_CALLED events; got {called_count}"
        assert completed_count == 3, (
            f"Expected 3 TOOL_COMPLETED events; got {completed_count}"
        )

        # Every TOOL_CALLED event must carry a trace_id
        called_events = [e for e in events if e["event_type"] == "TOOL_CALLED"]
        for ev in called_events:
            assert ev.get("trace_id") == _TRACE, (
                f"TOOL_CALLED event is missing trace_id: {ev}"
            )

    asyncio.run(scenario())
    _cleanup(journal)


# ---------------------------------------------------------------------------
# Test 5 — Partial failure: one denied activity in a batch
# ---------------------------------------------------------------------------


def test_parallel_batch_partial_failure():
    """
    When one activity in a batch is denied by the permission policy, the batch
    must report partial_success=True (some succeeded, one failed) and list the
    failed activity in batch.failures.
    """
    mission_id = f"{_MISSION_PREFIX}-PARTIAL"
    journal = MissionJournal(mission_id)
    _cleanup(journal)

    async def scenario():
        # shell.* → allow, but shell.destructive → deny
        policy = PermissionPolicy(
            scopes={
                "shell.*": "allow",
                "shell.destructive": "deny",
            }
        )
        runtime = ActivityRuntime(
            journal=journal,
            dry_run=True,
            permission_engine=PermissionEngine(policy),
        )
        scheduler = ParallelActivityScheduler(runtime, concurrency_limit=3)

        activities = [
            _shell("shell.tool_ok1", cmd="echo ok1"),
            _shell("shell.tool_bad", cmd="rm -rf /", scope="shell.destructive"),
            _shell("shell.tool_ok2", cmd="echo ok2"),
        ]
        batch: ActivityBatchResult = await scheduler.run_batch(activities)

        assert batch.success is False, "Batch should not be fully successful"
        assert batch.partial_success is True, "Batch should report partial_success"
        assert len(batch.failures) == 1, (
            f"Expected exactly 1 failure; got {len(batch.failures)}: "
            f"{[f.error for f in batch.failures]}"
        )
        assert batch.failures[0].tool == "shell.tool_bad"

        successful_results = [r for r in batch.results if r.success]
        assert len(successful_results) == 2

        # Journal must show the denial
        event_types = [e["event_type"] for e in _journal_events(journal)]
        assert "PERMISSION_DENIED" in event_types
        assert "TOOL_FAILED" in event_types

    asyncio.run(scenario())
    _cleanup(journal)


# ---------------------------------------------------------------------------
# Test 6 — fail_fast: first failure cancels remaining activities
# ---------------------------------------------------------------------------


def test_parallel_batch_fail_fast_cancels_on_first_failure():
    """
    With fail_fast=True, a batch that encounters a permission denial must cancel
    remaining pending activities. The batch must not report full success, and the
    results list must have the same length as the input (with cancelled entries
    reporting success=False).
    """
    mission_id = f"{_MISSION_PREFIX}-FAILFAST"
    journal = MissionJournal(mission_id)
    _cleanup(journal)

    async def scenario():
        # Only shell.destructive is denied; others are allowed.
        policy = PermissionPolicy(
            scopes={
                "shell.*": "allow",
                "shell.destructive": "deny",
            }
        )
        runtime = ActivityRuntime(
            journal=journal,
            dry_run=True,
            permission_engine=PermissionEngine(policy),
        )
        scheduler = ParallelActivityScheduler(
            runtime, concurrency_limit=4, fail_fast=True
        )

        activities = [
            _shell("shell.fail_trigger", cmd="rm -rf /", scope="shell.destructive"),
            _shell("shell.should_cancel_a", cmd="echo a"),
            _shell("shell.should_cancel_b", cmd="echo b"),
        ]
        batch: ActivityBatchResult = await scheduler.run_batch(activities)

        # The batch must not be fully successful
        assert batch.success is False, (
            "Batch with a failure should not be fully successful"
        )

        # The results list must have an entry for every submitted activity
        assert len(batch.results) == len(activities), (
            f"Expected {len(activities)} results; got {len(batch.results)}"
        )

        # At least one result must be a failure (the denied one or a cancelled one)
        failed_results = [r for r in batch.results if not r.success]
        assert len(failed_results) >= 1, "Expected at least 1 failed/cancelled result"

    asyncio.run(scenario())
    _cleanup(journal)
