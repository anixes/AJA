import pytest
import os
import json
import time
from pathlib import Path
from aja.memory.secretary import get_aja_memory
from aja.runtime.mission_journal import MissionJournal, MissionReducer, rebuild_mission_projections, rebuild_all_mission_projections
from aja.runtime.scheduler_journal import SchedulerJournal, SchedulerReducer, rebuild_scheduler_projections
from aja.learning.exploration import exploration_controller

def test_mission_journal_and_reducer():
    """
    Test emitting events to MissionJournal and using MissionReducer to rehydrate MissionState.
    """
    mission_id = "TEST-M-001"
    
    # Ensure previous test journal is cleaned up
    journal = MissionJournal(mission_id)
    if journal.journal_path.exists():
        journal.journal_path.unlink()
        
    # 1. Create a mission event
    journal.emit("MISSION_CREATED", {
        "goal": "Test deterministic mission event sourcing",
        "priority": 2,
        "metadata": {"source": "pytest"}
    })
    
    # 2. Start a run event
    journal.emit("MISSION_RUN_STARTED", {
        "run_id": "RUN-001",
        "trace_id": "TRACE-001"
    })
    
    # 3. Plan generated event
    journal.emit("MISSION_PLAN_GENERATED", {
        "plan_id": "PLAN-001"
    })
    
    # 4. Status change
    journal.emit("MISSION_STATUS_CHANGED", {
        "from": "PENDING",
        "to": "ACTIVE"
    })
    
    # 5. Epsilon / exploration update event
    journal.emit("EXPLORATION_STATE_UPDATED", {
        "exploration_state": {
            "epsilon": 0.15,
            "strategy_usage": {"experimental": 3}
        }
    })
    
    # 6. Complete mission
    journal.emit("MISSION_COMPLETED", {
        "success": True,
        "result_summary": "All tests passed with flying colors."
    })
    
    # Read and fold events
    events = journal.read_events()
    assert len(events) == 6
    assert all(e["event_schema_version"] == "1.0" for e in events)
    assert [e["sequence"] for e in events] == [0, 1, 2, 3, 4, 5]
    
    reducer = MissionReducer()
    state = reducer.reduce(events)
    
    assert state.mission_id == mission_id
    assert state.goal == "Test deterministic mission event sourcing"
    assert state.priority == 2
    assert state.active_run_id == "RUN-001"
    assert state.active_trace_id == "TRACE-001"
    assert state.plan_id == "PLAN-001"
    assert state.status == "DONE"
    assert state.exploration_state == {"epsilon": 0.15, "strategy_usage": {"experimental": 3}}
    assert state.result_summary == "All tests passed with flying colors."
    
    # Clean up journal file
    if journal.journal_path.exists():
        journal.journal_path.unlink()

def test_mission_projection_rebuild():
    """
    Test rebuilding the LanceDB mission projection from the event journal.
    """
    mission_id = "TEST-M-002"
    journal = MissionJournal(mission_id)
    if journal.journal_path.exists():
        journal.journal_path.unlink()
        
    # Write a clean sequence of events
    journal.emit("MISSION_CREATED", {"goal": "Rebuild Projection", "priority": 1, "metadata": {}})
    journal.emit("MISSION_STATUS_CHANGED", {"from": "PENDING", "to": "ACTIVE"})
    
    # Fetch from memory secretary and assert table contains the row
    mem = get_aja_memory()
    row = mem.get_mission(mission_id)
    assert row is not None
    assert row["status"] == "ACTIVE"
    assert row["goal"] == "Rebuild Projection"
    
    # Delete from LanceDB directly to simulate database loss
    table = mem.db.open_table("aja_missions")
    table.delete(f"mission_id = '{mission_id}'")
    assert mem.get_mission(mission_id) is None
    
    # Rebuild from journal!
    rebuild_mission_projections(mission_id)
    
    # Assert reconstructed row is identical
    rebuilt_row = mem.get_mission(mission_id)
    assert rebuilt_row is not None
    assert rebuilt_row["status"] == "ACTIVE"
    assert rebuilt_row["goal"] == "Rebuild Projection"
    
    # Rebuild all
    table = mem.db.open_table("aja_missions")
    table.delete(f"mission_id = '{mission_id}'")
    assert mem.get_mission(mission_id) is None
    rebuild_all_mission_projections()
    assert mem.get_mission(mission_id) is not None
    
    # Clean up
    if journal.journal_path.exists():
        journal.journal_path.unlink()
    table = mem.db.open_table("aja_missions")
    table.delete(f"mission_id = '{mission_id}'")

def test_scheduler_journal_and_reducer():
    """
    Test SchedulerJournal and SchedulerReducer rehydration.
    """
    # Scheduler journal is a global single journal
    journal = SchedulerJournal()
    
    # Ensure any leftovers are purged from journal
    filtered = [e for e in journal.read_events() if e.get("job_id") != "TEST-JOB-999"]
    with journal.journal_path.open("w", encoding="utf-8") as f:
        for ev in filtered:
            f.write(json.dumps(ev) + "\n")
            
    original_events = journal.read_events()
    job_id = "TEST-JOB-999"
    
    # Register job
    journal.emit("SCHEDULER_JOB_REGISTERED", {
        "job_id": job_id,
        "goal": "Run diagnostic check",
        "schedule_expr": "*/5 * * * *"
    })
    
    # Fire job
    journal.emit("SCHEDULER_JOB_FIRED", {
        "job_id": job_id,
        "run_id": "RUN-SCH-1",
        "trace_id": "TRACE-SCH-1",
        "tick": 42,
        "timestamp_ts": 1234567.89
    })
    
    # Pause job
    journal.emit("SCHEDULER_JOB_PAUSED", {
        "job_id": job_id
    })
    
    # Resume job
    journal.emit("SCHEDULER_JOB_RESUMED", {
        "job_id": job_id
    })
    
    # Complete job
    journal.emit("SCHEDULER_JOB_COMPLETED", {
        "job_id": job_id,
        "run_id": "RUN-SCH-1"
    })
    
    # Read events and filter for our test job
    events = [e for e in journal.read_events() if e.get("job_id") == job_id]
    assert len(events) == 5
    
    reducer = SchedulerReducer()
    jobs = reducer.reduce(events)
    assert job_id in jobs
    
    job = jobs[job_id]
    assert job.job_id == job_id
    assert job.goal == "Run diagnostic check"
    assert job.schedule_expr == "*/5 * * * *"
    assert job.last_run == 1234567.89
    assert job.last_run_tick == 42
    assert job.paused is False
    assert job.deleted is False
    assert job.active_run_id is None # Cleared by completed event
    
    # Restore original events by truncating the journal or dropping our test events
    # We can just read all events, filter our job_id out, and overwrite
    all_events = journal.read_events()
    filtered = [e for e in all_events if e.get("job_id") != job_id]
    
    # Overwrite journal with filtered events to keep it clean
    with journal.journal_path.open("w", encoding="utf-8") as f:
        for ev in filtered:
            f.write(json.dumps(ev) + "\n")

def test_scheduler_projection_rebuild():
    """
    Test rebuilding scheduler table projections from scheduler journal.
    """
    mem = get_aja_memory()
    job_id = "TEST-JOB-888"
    
    # Ensure any leftovers are purged from database and journal
    table = mem.db.open_table("aja_tasks")
    table.delete(f"task_id = '{job_id}'")
    
    journal = SchedulerJournal()
    filtered = [e for e in journal.read_events() if e.get("job_id") != job_id]
    with journal.journal_path.open("w", encoding="utf-8") as f:
        for ev in filtered:
            f.write(json.dumps(ev) + "\n")
            
    # Register job using standard creation which write-throughs scheduler journal
    task = mem.create_task({
        "task_id": job_id,
        "context": "Verification schedule task",
        "owner": "scheduler",
        "metadata": {
            "schedule_expr": "every 15m",
            "paused": True
        }
    })
    
    # Assert it was created and has scheduled_paused status
    row = mem.get_task(job_id)
    assert row is not None
    assert row["status"] == "scheduled_paused"
    assert row["owner"] == "scheduler"
    
    # Delete from LanceDB directly
    table = mem.db.open_table("aja_tasks")
    table.delete(f"task_id = '{job_id}'")
    assert mem.get_task(job_id) is None
    
    # Rebuild from journal!
    rebuild_scheduler_projections()
    
    rebuilt = mem.get_task(job_id)
    assert rebuilt is not None
    assert rebuilt["status"] == "scheduled_paused"
    assert rebuilt["owner"] == "scheduler"
    
    # Clean up journal file from our job_id
    journal = SchedulerJournal()
    all_events = journal.read_events()
    filtered = [e for e in all_events if e.get("job_id") != job_id]
    with journal.journal_path.open("w", encoding="utf-8") as f:
        for ev in filtered:
            f.write(json.dumps(ev) + "\n")
            
    # Remove from table
    table.delete(f"task_id = '{job_id}'")

def test_exploration_state_serialization():
    """
    Test that ExplorationController load_from_mission and save_to_mission
    correctly preserve and restore exploration parameters.
    """
    mission_id = "TEST-M-EXPLORE"
    journal = MissionJournal(mission_id)
    if journal.journal_path.exists():
        journal.journal_path.unlink()
        
    # Reset controller to a known state
    exploration_controller.epsilon = 0.5
    exploration_controller.strategy_usage = {"alpha": 5, "beta": 2}
    exploration_controller.total_usages = 7
    
    # Save exploration state to mission
    exploration_controller.save_to_mission(mission_id)
    
    # Mutate state on controller
    exploration_controller.epsilon = 0.1
    exploration_controller.strategy_usage = {"gamma": 10}
    exploration_controller.total_usages = 10
    
    # Reload from mission journal!
    exploration_controller.load_from_mission(mission_id)
    
    # Epsilon and strategy usage should be restored deterministically
    assert exploration_controller.epsilon == 0.5
    assert exploration_controller.strategy_usage == {"alpha": 5, "beta": 2}
    assert exploration_controller.total_usages == 7
    
    # Clean up
    if journal.journal_path.exists():
        journal.journal_path.unlink()
