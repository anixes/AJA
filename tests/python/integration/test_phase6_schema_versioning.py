import pytest
import os
import json
import logging
from pathlib import Path
from aja.memory.secretary import get_aja_memory
from aja.runtime.execution.rehydrator import EventRehydrator
from aja.runtime.event_schema import REDUCERS, EVENT_SCHEMAS
from aja.main import cmd_rebuild_projections
from aja.runtime.mission_journal import MissionJournal
from aja.runtime.scheduler_journal import SchedulerJournal

def test_event_schema_registry_structure():
    """
    Assert that core event schemas and reducers are correctly defined.
    """
    assert "EXECUTION_STATE_CHANGED" in EVENT_SCHEMAS
    assert "MISSION_CREATED" in EVENT_SCHEMAS
    assert "SCHEDULER_JOB_FIRED" in EVENT_SCHEMAS
    
    assert ("EXECUTION_STATE_CHANGED", "1.0") in REDUCERS
    assert ("EXECUTION_SESSION_FINISHED", "1.0") in REDUCERS

def test_versioned_rehydration_graceful_skipping(caplog):
    """
    Assert that the VersionedEventRehydrator gracefully skips unrecognized event versions
    and issues a warning log, rather than crashing the rehydration loop.
    """
    session_id = "TEST-SESS-V6-SKIP"
    from aja.config import PROJECT_ROOT
    session_dir = PROJECT_ROOT / ".aja" / "executions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    
    manifest_path = session_dir / "manifest.json"
    timeline_path = session_dir / "timeline.jsonl"
    
    # 1. Write manifest
    manifest_path.write_text(json.dumps({
        "session_id": session_id,
        "created_at": "2026-05-26T12:00:00Z",
        "command": "echo v6",
        "backend": "direct",
        "metadata": {
            "request": {
                "command": "echo v6",
                "env": {},
                "metadata": {}
            }
        }
    }), encoding="utf-8")
    
    # 2. Write timeline containing an unrecognized version (e.g., version 2.0 of EXECUTION_STDOUT)
    # The TelemetryEmitter frames each event. We format them with the FRAME prefix.
    import zlib
    
    event_created_to_starting = {
        "event_type": "EXECUTION_STATE_CHANGED",
        "event_schema_version": "1.0",
        "sequence": 0,
        "timestamp": "2026-05-26T12:00:01Z",
        "from": "created",
        "to": "starting"
    }
    event_starting_to_running = {
        "event_type": "EXECUTION_STATE_CHANGED",
        "event_schema_version": "1.0",
        "sequence": 1,
        "timestamp": "2026-05-26T12:00:01.5Z",
        "from": "starting",
        "to": "running"
    }
    event_stdout_unrecognized = {
        "event_type": "EXECUTION_STDOUT",
        "event_schema_version": "2.0", # Unrecognized version!
        "sequence": 2,
        "timestamp": "2026-05-26T12:00:02Z",
        "line": "Skip this unrecognized stdout\n"
    }
    event_stdout_recognized = {
        "event_type": "EXECUTION_STDOUT",
        "event_schema_version": "1.0", # Recognized version
        "sequence": 3,
        "timestamp": "2026-05-26T12:00:03Z",
        "line": "Keep this recognized stdout\n"
    }
    event_process_exited = {
        "event_type": "EXECUTION_PROCESS_EXITED",
        "event_schema_version": "1.0",
        "sequence": 4,
        "timestamp": "2026-05-26T12:00:03.5Z",
        "exit_code": 0
    }
    event_running_to_completed = {
        "event_type": "EXECUTION_STATE_CHANGED",
        "event_schema_version": "1.0",
        "sequence": 5,
        "timestamp": "2026-05-26T12:00:04Z",
        "from": "running",
        "to": "completed"
    }
    event_finished = {
        "event_type": "EXECUTION_SESSION_FINISHED",
        "event_schema_version": "1.0",
        "sequence": 6,
        "timestamp": "2026-05-26T12:00:05Z",
        "duration_ms": 100
    }
    
    def get_frame(payload_dict):
        payload = json.dumps(payload_dict)
        pl_len = hex(len(payload.encode("utf-8")))[2:]
        crc = hex(zlib.crc32(payload.encode("utf-8")) & 0xffffffff)[2:]
        return f"FRAME:{pl_len}:{crc}:{payload}"
        
    timeline_path.write_text(
        "\n".join([get_frame(ev) for ev in [
            event_created_to_starting,
            event_starting_to_running,
            event_stdout_unrecognized,
            event_stdout_recognized,
            event_process_exited,
            event_running_to_completed,
            event_finished
        ]]) + "\n",
        encoding="utf-8"
    )
    
    try:
        # Rehydrate and assert it skips 2.0 but folds 1.0 successfully without raising exceptions
        rehydrator = EventRehydrator(session_id, base_dir=PROJECT_ROOT / ".aja" / "executions")
        
        with caplog.at_level(logging.WARNING):
            session = rehydrator.rehydrate()
            
        assert session is not None
        assert session.state == "completed"
        assert session.result is not None
        assert session.result.stdout == "Keep this recognized stdout\n"
        assert session.result.success is True
        
        # Verify that a warning log was issued for the unrecognized version
        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("No reducer found" in w for w in warnings)
        
    finally:
        # Clean up session directory
        if manifest_path.exists():
            manifest_path.unlink()
        if timeline_path.exists():
            timeline_path.unlink()
        if session_dir.exists():
            session_dir.rmdir()

def test_cli_projection_rebuilding_command():
    """
    Verify that the rebuild-projections CLI command correctly iterates over
    journals and regeneratesderived projections in LanceDB.
    """
    mem = get_aja_memory()
    mission_id = "TEST-M-V6"
    job_id = "TEST-JOB-V6"
    
    # Clean previous journal files if any
    mission_journal = MissionJournal(mission_id)
    if mission_journal.journal_path.exists():
        mission_journal.journal_path.unlink()
        
    scheduler_journal = SchedulerJournal()
    original_events = scheduler_journal.read_events()
    
    # 1. Emit to journals
    mission_journal.emit("MISSION_CREATED", {"goal": "CLI Rebuild Test", "priority": 3, "metadata": {}})
    mission_journal.emit("MISSION_STATUS_CHANGED", {"from": "PENDING", "to": "ACTIVE"})
    
    scheduler_journal.emit("SCHEDULER_JOB_REGISTERED", {
        "job_id": job_id,
        "goal": "Rebuild cron verification",
        "schedule_expr": "0 * * * *"
    })
    scheduler_journal.emit("SCHEDULER_JOB_PAUSED", {"job_id": job_id})
    
    # 2. Assert they were projected
    assert mem.get_mission(mission_id) is not None
    assert mem.get_task(job_id) is not None
    
    # 3. Simulate total DB data loss by deleting from LanceDB
    mission_table = mem.db.open_table("aja_missions")
    task_table = mem.db.open_table("aja_tasks")
    
    mission_table.delete(f"mission_id = '{mission_id}'")
    task_table.delete(f"task_id = '{job_id}'")
    
    # Re-open table reference to ensure caching doesn't mask deletion
    assert mem.db.open_table("aja_missions").search().where(f"mission_id = '{mission_id}'").to_list() == []
    assert mem.db.open_table("aja_tasks").search().where(f"task_id = '{job_id}'").to_list() == []
    
    # 4. Trigger CLI rebuild projections!
    cmd_rebuild_projections()
    
    # 5. Assert derived read projections are fully restored
    rebuilt_mission = mem.db.open_table("aja_missions").search().where(f"mission_id = '{mission_id}'").to_list()
    rebuilt_task = mem.db.open_table("aja_tasks").search().where(f"task_id = '{job_id}'").to_list()
    
    assert rebuilt_mission != []
    assert rebuilt_mission[0]["status"] == "ACTIVE"
    assert rebuilt_mission[0]["goal"] == "CLI Rebuild Test"
    
    assert rebuilt_task != []
    assert rebuilt_task[0]["status"] == "scheduled_paused"
    assert rebuilt_task[0]["context"] == "Rebuild cron verification"
    
    # Clean up journal files
    if mission_journal.journal_path.exists():
        mission_journal.journal_path.unlink()
        
    filtered = [e for e in scheduler_journal.read_events() if e.get("job_id") != job_id]
    with scheduler_journal.journal_path.open("w", encoding="utf-8") as f:
        for ev in filtered:
            f.write(json.dumps(ev) + "\n")
            
    # Clean up DB
    mem.db.open_table("aja_missions").delete(f"mission_id = '{mission_id}'")
    mem.db.open_table("aja_tasks").delete(f"task_id = '{job_id}'")
