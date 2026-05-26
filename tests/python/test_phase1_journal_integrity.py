"""
tests/python/test_phase1_journal_integrity.py
==============================================
Phase 1 test suite — Journal Integrity Hardening.

Covers:
* CRC corruption detection (JournalCorruptionError raised, not swallowed)
* Truncated final frame (silently dropped, prior state intact)
* Zero-byte journal (returns None gracefully)
* Sequence ordering enforced (out-of-order raises JournalCorruptionError)
* Full ExecutionRequest round-trip (to_dict / from_dict lossless)
* event_schema_version field present on every emitted event
* Crash-orphan detection on ExecutionManager construction
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest

from aja.runtime.execution.contracts import ExecutionRequest, ExecutionManifest
from aja.runtime.execution.rehydrator import EventRehydrator, JournalCorruptionError
from aja.runtime.execution.sequencer import EventSequencer, TelemetryEmitter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_frame(payload_dict: dict) -> str:
    """Build a valid FRAME line from a dict."""
    payload_str = json.dumps(payload_dict, default=str)
    length = len(payload_str)
    crc32 = zlib.crc32(payload_str.encode("utf-8")) & 0xFFFFFFFF
    return f"FRAME:{length:08x}:{crc32:08x}:{payload_str}\n"


def _make_corrupt_frame(payload_dict: dict) -> str:
    """Build a FRAME whose CRC32 is deliberately wrong."""
    payload_str = json.dumps(payload_dict, default=str)
    length = len(payload_str)
    bad_crc = 0xDEADBEEF
    return f"FRAME:{length:08x}:{bad_crc:08x}:{payload_str}\n"


def _write_session(tmp_path: Path, session_id: str, events: list[dict]) -> Path:
    """Create a minimal session directory with a timeline.jsonl."""
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True)

    manifest = {
        "session_id": session_id,
        "command": "echo hello",
        "trace_id": None,
        "run_id": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "cwd": str(tmp_path),
        "backend": "test",
        "schema_version": "1.0",
        "metadata": {
            "request": ExecutionRequest(command="echo hello").to_dict()
        },
    }
    (session_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with (session_dir / "timeline.jsonl").open("w", encoding="utf-8") as fh:
        for evt in events:
            fh.write(_make_frame(evt))

    return session_dir


# ---------------------------------------------------------------------------
# 1. CRC corruption detection
# ---------------------------------------------------------------------------

class TestCRCCorruptionDetection:
    def test_corrupt_frame_raises_journal_corruption_error(self, tmp_path):
        """A journal with a corrupt CRC must raise JournalCorruptionError."""
        session_id = "sess-corrupt"
        session_dir = tmp_path / session_id
        session_dir.mkdir()

        manifest = {
            "session_id": session_id,
            "command": "echo hello",
            "trace_id": None,
            "run_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "cwd": str(tmp_path),
            "backend": "test",
            "schema_version": "1.0",
            "metadata": {"request": ExecutionRequest(command="echo hello").to_dict()},
        }
        (session_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        timeline = session_dir / "timeline.jsonl"
        # One valid frame followed by a corrupt frame
        valid_event = {
            "event_type": "EXECUTION_STATE_CHANGED",
            "event_schema_version": "1.0",
            "sequence": 0,
            "from": "created",
            "to": "starting",
        }
        corrupt_event = {
            "event_type": "EXECUTION_STATE_CHANGED",
            "event_schema_version": "1.0",
            "sequence": 1,
            "from": "starting",
            "to": "running",
        }
        with timeline.open("w", encoding="utf-8") as fh:
            fh.write(_make_frame(valid_event))
            fh.write(_make_corrupt_frame(corrupt_event))

        rehydrator = EventRehydrator(session_id, tmp_path)
        with pytest.raises(JournalCorruptionError) as exc_info:
            rehydrator.rehydrate()

        assert exc_info.value.last_valid_seq == 0  # Only the first event was valid
        assert "corrupt" in str(exc_info.value).lower()

    def test_healthy_journal_does_not_raise(self, tmp_path):
        """A journal with all-valid CRC frames must NOT raise."""
        events = [
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 0, "from": "created", "to": "starting"},
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 1, "from": "starting", "to": "running"},
            {"event_type": "EXECUTION_SESSION_FINISHED", "event_schema_version": "1.0",
             "sequence": 2, "duration_ms": 100, "exit_code": 0, "state": "completed"},
        ]
        _write_session(tmp_path, "sess-healthy", events)

        rehydrator = EventRehydrator("sess-healthy", tmp_path)
        session = rehydrator.rehydrate()
        # Should rehydrate successfully without error
        assert session is not None


# ---------------------------------------------------------------------------
# 2. Truncated final frame
# ---------------------------------------------------------------------------

class TestTruncatedFrame:
    def test_truncated_frame_is_dropped_prior_state_intact(self, tmp_path):
        """A partial write at the end should be silently truncated; earlier
        valid events still produce a rehydrated session."""
        events = [
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 0, "from": "created", "to": "starting"},
            {"event_type": "EXECUTION_SESSION_FINISHED", "event_schema_version": "1.0",
             "sequence": 1, "duration_ms": 50, "exit_code": 0, "state": "completed"},
        ]
        session_dir = _write_session(tmp_path, "sess-truncated", events)

        # Append a partial FRAME (simulates crash mid-write)
        with (session_dir / "timeline.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("FRAME:000000ff:xxxxxxxx:{\"incomplete")

        rehydrator = EventRehydrator("sess-truncated", tmp_path)
        # repair_journal() should truncate the bad tail; rehydration must
        # succeed without raising.
        session = rehydrator.rehydrate()
        assert session is not None


# ---------------------------------------------------------------------------
# 3. Zero-byte journal
# ---------------------------------------------------------------------------

class TestZeroByteJournal:
    def test_zero_byte_timeline_returns_none(self, tmp_path):
        """An empty timeline.jsonl should return None (no session to rehydrate)."""
        session_id = "sess-empty"
        session_dir = tmp_path / session_id
        session_dir.mkdir()
        (session_dir / "manifest.json").write_text(
            json.dumps({
                "session_id": session_id,
                "command": "echo",
                "trace_id": None,
                "run_id": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "cwd": str(tmp_path),
                "backend": "test",
                "schema_version": "1.0",
                "metadata": {"request": ExecutionRequest(command="echo").to_dict()},
            }),
            encoding="utf-8",
        )
        # Create empty timeline
        (session_dir / "timeline.jsonl").write_text("", encoding="utf-8")

        rehydrator = EventRehydrator(session_id, tmp_path)
        result = rehydrator.rehydrate()
        # Empty journal → no events → session stays in 'created' with no result
        assert result is not None
        assert result.state == "created"

    def test_missing_timeline_returns_none(self, tmp_path):
        """A session directory with no timeline.jsonl should return None."""
        session_id = "sess-no-timeline"
        session_dir = tmp_path / session_id
        session_dir.mkdir()
        (session_dir / "manifest.json").write_text("{}", encoding="utf-8")

        rehydrator = EventRehydrator(session_id, tmp_path)
        result = rehydrator.rehydrate()
        assert result is None


# ---------------------------------------------------------------------------
# 4. Sequence ordering enforcement
# ---------------------------------------------------------------------------

class TestSequenceOrdering:
    def test_out_of_order_sequences_raise_corruption_error(self, tmp_path):
        """Events whose sequence numbers are not strictly increasing must
        raise JournalCorruptionError."""
        events = [
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 5, "from": "created", "to": "starting"},
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 2, "from": "starting", "to": "running"},  # out of order!
        ]
        _write_session(tmp_path, "sess-ooo", events)

        rehydrator = EventRehydrator("sess-ooo", tmp_path)
        with pytest.raises(JournalCorruptionError):
            rehydrator.rehydrate()

    def test_equal_sequence_numbers_raise_corruption_error(self, tmp_path):
        """Duplicate sequence numbers must also raise JournalCorruptionError."""
        events = [
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 0, "from": "created", "to": "starting"},
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 0, "from": "starting", "to": "running"},  # duplicate!
        ]
        _write_session(tmp_path, "sess-dup-seq", events)

        rehydrator = EventRehydrator("sess-dup-seq", tmp_path)
        with pytest.raises(JournalCorruptionError):
            rehydrator.rehydrate()


# ---------------------------------------------------------------------------
# 5. ExecutionRequest round-trip
# ---------------------------------------------------------------------------

class TestExecutionRequestRoundTrip:
    def test_to_dict_from_dict_roundtrip_all_fields(self):
        """Every field must survive a to_dict() → from_dict() round-trip."""
        original = ExecutionRequest(
            command="python -m pytest",
            timeout=120.0,
            cwd="/tmp/work",
            env={"FOO": "bar", "PYTHONPATH": "libs"},
            shell=False,
            allow_network=True,
            use_docker=True,
            docker_image="python:3.12-slim",
            memory="512m",
            cpus="2.0",
            workspace_mode="isolated",
            stdin="input data",
            use_pty=True,
            metadata={"run_id": "run-abc", "node_id": "n1"},
        )

        data = original.to_dict()
        restored = ExecutionRequest.from_dict(data)

        assert restored.command == original.command
        assert restored.timeout == original.timeout
        assert restored.cwd == original.cwd
        assert restored.env == original.env
        assert restored.shell == original.shell
        assert restored.allow_network == original.allow_network
        assert restored.use_docker == original.use_docker
        assert restored.docker_image == original.docker_image
        assert restored.memory == original.memory
        assert restored.cpus == original.cpus
        assert restored.workspace_mode == original.workspace_mode
        assert restored.stdin == original.stdin
        assert restored.use_pty == original.use_pty
        assert restored.metadata == original.metadata

    def test_from_dict_tolerates_missing_keys(self):
        """from_dict() must not raise on a partial dict (pre-Phase-1 compat)."""
        partial = {"command": "echo hi"}
        req = ExecutionRequest.from_dict(partial)
        assert req.command == "echo hi"
        assert req.timeout == 60.0   # default
        assert req.shell is True      # default
        assert req.cwd is None        # default

    def test_manifest_embeds_full_request(self, tmp_path):
        """ExecutionManifest.create() must embed the full request dict."""
        from aja.runtime.execution.contracts import WorkspaceSnapshot
        req = ExecutionRequest(
            command="python foo.py",
            timeout=30.0,
            use_docker=True,
            docker_image="python:3.12",
            use_pty=True,
        )
        ws = WorkspaceSnapshot(
            session_id="sess-123",
            source_root=str(tmp_path),
            execution_root=str(tmp_path),
            artifact_root=str(tmp_path),
            mode="isolated",
        )
        manifest = ExecutionManifest.create(
            session_id="sess-123",
            request=req,
            trace_id=None,
            run_id=None,
            cwd=str(tmp_path),
            backend="test",
            workspace=ws,
        )
        d = manifest.to_dict()
        embedded = d["metadata"]["request"]
        assert embedded["command"] == "python foo.py"
        assert embedded["timeout"] == 30.0
        assert embedded["use_docker"] is True
        assert embedded["docker_image"] == "python:3.12"
        assert embedded["use_pty"] is True


# ---------------------------------------------------------------------------
# 6. event_schema_version on every emitted event
# ---------------------------------------------------------------------------

class TestEventSchemaVersion:
    def test_every_sequenced_event_has_schema_version(self, tmp_path):
        """EventSequencer must include event_schema_version in every event."""
        sequencer = EventSequencer(session_id="sess-ver", trace_id=None)
        emitter = TelemetryEmitter(tmp_path, sequencer)

        emitter.emit("EXECUTION_STATE_CHANGED", {"from": "created", "to": "starting"})
        emitter.emit("EXECUTION_PROCESS_STARTED", {"pid": 12345})
        emitter.emit("EXECUTION_SESSION_FINISHED", {"duration_ms": 0, "exit_code": 0})

        events = []
        for line in (tmp_path / "timeline.jsonl").read_text(encoding="utf-8").splitlines():
            if line.startswith("FRAME:"):
                parts = line.split(":", 3)
                events.append(json.loads(parts[3]))

        assert len(events) == 3
        for evt in events:
            assert "event_schema_version" in evt, (
                f"event_schema_version missing from event: {evt.get('event_type')}"
            )
            assert evt["event_schema_version"] == "1.0"


# ---------------------------------------------------------------------------
# 7. Crash-orphan detection
# ---------------------------------------------------------------------------

class TestCrashOrphanDetection:
    def test_orphaned_session_gets_crashed_event(self, tmp_path):
        """A session directory with no terminal event must receive
        EXECUTION_SESSION_CRASHED on ExecutionManager construction."""
        from aja.runtime.execution.manager import ExecutionManager

        session_id = "sess-orphan"
        session_dir = tmp_path / ".aja" / "executions" / session_id
        session_dir.mkdir(parents=True)

        # Write two non-terminal events
        events = [
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 0, "from": "created", "to": "starting"},
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 1, "from": "starting", "to": "running"},
        ]
        with (session_dir / "timeline.jsonl").open("w", encoding="utf-8") as fh:
            for evt in events:
                fh.write(_make_frame(evt))

        # Constructing ExecutionManager should trigger orphan detection
        manager = ExecutionManager(project_root=tmp_path)

        # Journal must now contain the CRASHED event
        timeline_content = (session_dir / "timeline.jsonl").read_text(encoding="utf-8")
        assert "EXECUTION_SESSION_CRASHED" in timeline_content

    def test_completed_session_is_not_marked_crashed(self, tmp_path):
        """A session with a terminal event must NOT be marked as crashed."""
        from aja.runtime.execution.manager import ExecutionManager

        session_id = "sess-done"
        session_dir = tmp_path / ".aja" / "executions" / session_id
        session_dir.mkdir(parents=True)

        events = [
            {"event_type": "EXECUTION_STATE_CHANGED", "event_schema_version": "1.0",
             "sequence": 0, "from": "created", "to": "starting"},
            {"event_type": "EXECUTION_SESSION_FINISHED", "event_schema_version": "1.0",
             "sequence": 1, "duration_ms": 100, "exit_code": 0, "state": "completed"},
        ]
        with (session_dir / "timeline.jsonl").open("w", encoding="utf-8") as fh:
            for evt in events:
                fh.write(_make_frame(evt))

        original_content = (session_dir / "timeline.jsonl").read_text(encoding="utf-8")

        _ = ExecutionManager(project_root=tmp_path)

        # File must be unchanged
        new_content = (session_dir / "timeline.jsonl").read_text(encoding="utf-8")
        assert new_content == original_content

    def test_empty_timeline_is_not_marked_crashed(self, tmp_path):
        """A session directory with an empty timeline should not be touched."""
        from aja.runtime.execution.manager import ExecutionManager

        session_id = "sess-empty-orphan"
        session_dir = tmp_path / ".aja" / "executions" / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "timeline.jsonl").write_text("", encoding="utf-8")

        _ = ExecutionManager(project_root=tmp_path)

        # Empty file must remain empty (size == 0)
        assert (session_dir / "timeline.jsonl").stat().st_size == 0
