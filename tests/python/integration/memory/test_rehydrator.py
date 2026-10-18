import json
from pathlib import Path

from aja.runtime.execution.contracts import ExecutionState
from aja.runtime.execution.rehydrator import EventRehydrator
from aja.runtime.execution.sequencer import TelemetryEmitter, EventSequencer

def test_rehydrator_rebuilds_session_from_journal(tmp_path: Path):
    session_id = "exec-test-rehydrate-123"
    base_dir = tmp_path / ".aja" / "executions"
    root = base_dir / session_id
    root.mkdir(parents=True, exist_ok=True)
    
    # Write a fake manifest
    manifest_data = {
        "session_id": session_id,
        "command": "echo hello",
        "trace_id": "test-trace",
        "run_id": "test-run",
        "created_at": "2024-01-01T00:00:00Z",
        "cwd": "/tmp",
        "backend": "posix_pty_stream",
        "schema_version": "1.0",
        "metadata": {
            "request": {
                "command": "echo hello"
            }
        }
    }
    (root / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    
    # Write a fake timeline manually to simulate a crash
    # Instead of raw json, let's use the sequencer and emitter to generate a real journal
    sequencer = EventSequencer(session_id, "test-trace")
    emitter = TelemetryEmitter(root, sequencer)
    
    emitter.emit("EXECUTION_STATE_CHANGED", {"from": "created", "to": "starting", "state": "starting"})
    emitter.emit("EXECUTION_WORKSPACE_CREATED", {"workspace": {"session_id": session_id, "source_root": "", "execution_root": "", "artifact_root": "", "mode": "isolated"}})
    emitter.emit("EXECUTION_STATE_CHANGED", {"from": "starting", "to": "running", "state": "running"})
    emitter.emit("EXECUTION_PROCESS_STARTED", {"pid": 12345})
    emitter.emit("EXECUTION_STDOUT", {"line": "hello\n"})
    
    # Simulate a crash here (journal is written)
    
    # Now simulate recovery by repairing the journal
    TelemetryEmitter.repair_journal(emitter.timeline_path)
    
    # Now continue timeline as if recovered
    # Actually if it crashed during running, we can just fail it
    emitter.emit("EXECUTION_STATE_CHANGED", {"from": "running", "to": "failed", "state": "failed"})
    emitter.emit("EXECUTION_PROCESS_EXITED", {"pid": 12345, "exit_code": 1})
    emitter.emit("EXECUTION_SESSION_FINISHED", {"exit_code": 1, "duration_ms": 100, "state": "failed"})
    
    # Rehydrate
    rehydrator = EventRehydrator(session_id, base_dir)
    session = rehydrator.rehydrate()
    
    assert session is not None
    assert session.session_id == session_id
    assert session.state == "failed"
    assert session.pid == 12345
    assert session.returncode == 1
    assert session.result is not None
    assert session.result.stdout == "hello\n"
    assert session.result.success is False
    assert session.result.exit_code == 1
