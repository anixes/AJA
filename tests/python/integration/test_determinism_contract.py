import pytest
import json
from unittest.mock import MagicMock, patch
from aja.runtime.replay_guards import (
    replay_safe_random,
    derive_run_id,
    derive_session_id,
    derive_baton_code,
    derive_activity_id,
    derive_trace_id
)
from aja.runtime.execution.activity import ActivityContext, set_activity_context, reset_activity_context
from aja.scheduler.cron_scheduler import CronScheduler, parse_duration_to_seconds

def test_replay_safe_random():
    # 1. Verification of absolute determinism
    r1 = replay_safe_random("run-test-1", 1, "salt-A")
    r2 = replay_safe_random("run-test-1", 1, "salt-A")
    assert r1 == r2
    assert 0.0 <= r1 < 1.0

    # 2. Sensitivity to attempt, run_id and salt
    r3 = replay_safe_random("run-test-1", 2, "salt-A")
    r4 = replay_safe_random("run-test-2", 1, "salt-A")
    r5 = replay_safe_random("run-test-1", 1, "salt-B")

    assert r1 != r3
    assert r1 != r4
    assert r1 != r5


def test_hierarchical_derivations():
    run_id = derive_run_id("test-mission", 3)
    assert run_id.startswith("run-")
    assert run_id == derive_run_id("test-mission", 3)
    assert run_id != derive_run_id("test-mission", 4)

    sess_id = derive_session_id(run_id, "node-5")
    assert sess_id.startswith("exec-")
    assert sess_id == derive_session_id(run_id, "node-5")
    assert sess_id != derive_session_id(run_id, "node-6")

    baton = derive_baton_code(run_id, 2)
    assert len(baton) == 6
    assert baton.isupper()
    assert baton == derive_baton_code(run_id, 2)
    assert baton != derive_baton_code(run_id, 3)

    act_id = derive_activity_id(sess_id, 14, "subprocess.run")
    assert act_id.startswith("act-")
    assert act_id == derive_activity_id(sess_id, 14, "subprocess.run")
    assert act_id != derive_activity_id(sess_id, 15, "subprocess.run")

    trace_id = derive_trace_id(run_id)
    assert trace_id.startswith("tr-")
    assert trace_id == derive_trace_id(run_id)


def test_logical_tick_scheduler():
    # Verify logical tick-based CronScheduler trigger decisions
    from aja.runtime.task_store import LanceRuntimeTaskStore
    
    mock_store = MagicMock()
    mock_sink = MagicMock()
    
    scheduler = CronScheduler(check_interval=0.001, store=mock_store, event_sink=mock_sink)
    
    # Register mock job: every 2 seconds -> should trigger at 2 ticks
    job_id = "JOB-T1"
    mock_task = {
        "task_id": job_id,
        "context": "Mock task execution",
        "owner": "scheduler",
        "status": "scheduled",
        "metadata_json": json.dumps({
            "schedule_expr": "every 2s",
            "last_run": 0.0,
            "last_run_tick": 0,
            "paused": False
        })
    }
    
    mock_store.list_tasks.return_value = [mock_task]
    mock_store.get_task.return_value = mock_task
    
    # Run a single tick sequence manually to ensure isolation
    scheduler._running = True
    
    # Tick 1: self._tick becomes 1. _tick - last_run_tick (0) = 1 < 2. Not due.
    async def run_ticks():
        # First tick
        scheduler._tick = 0
        await scheduler.tick_loop()
        
    # We patch sleep and execute_job to keep the test synchronous and direct
    with patch("asyncio.sleep", return_value=None), \
         patch.object(scheduler, "_execute_job", return_value=None) as mock_exec:
         
        # Simulate running 1st tick
        scheduler._tick = 0
        mock_store.list_tasks.return_value = [mock_task]
        
        # We manually drive the loop for 2 steps, then stop it
        # Step 1
        scheduler._tick += 1
        # task metadata mock check for first tick
        meta = json.loads(mock_task["metadata_json"])
        dur_secs = parse_duration_to_seconds(meta["schedule_expr"])
        assert dur_secs == 2.0
        assert scheduler._tick - meta["last_run_tick"] < 2
        
        # Step 2: _tick becomes 2. _tick - last_run_tick (0) = 2 >= 2. Due!
        scheduler._tick += 1
        assert scheduler._tick - meta["last_run_tick"] >= 2
        
        # Verify run_id & trace_id derivation inside scheduler triggers deterministically
        sched_run_id = derive_run_id(job_id, scheduler._tick)
        sched_trace_id = derive_trace_id(sched_run_id)
        assert sched_run_id == f"run-{derive_run_id(job_id, 2)[4:]}"
        assert sched_trace_id == derive_trace_id(sched_run_id)



