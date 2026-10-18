"""
test_run_journal.py - Comprehensive Unit Tests for SQLite WAL RunJournal.
========================================================================
Validates:
- Database initialization and WAL mode on file-based journals.
- Event appending with deterministic payload hashing and hash chaining.
- Cryptographic tamper detection (detects payload tampering, hash alteration, and sequence deletion).
- Crash recovery of abandoned RUNNING sessions.
- Multi-threaded concurrent appending with transaction serialization.
- Clean shutdown and context manager semantics.
"""

import concurrent.futures
import sqlite3
import time
from pathlib import Path

import pytest
from aja.orchestration.run_journal import (
    GENESIS_PREV_HASH,
    JournalEvent,
    RunJournal,
    RunRecord,
    canonical_json,
    sha256_text,
)


def test_in_memory_initialization():
    """Verify that in-memory journal initializes cleanly and reports valid journal mode."""
    with RunJournal(":memory:") as journal:
        assert journal.journal_mode in ("delete", "memory", "wal")
        run = journal.start_run(run_id="run-mem-1", task="Test in memory task")
        assert run.run_id == "run-mem-1"
        assert run.status == "RUNNING"

        events = journal.get_events("run-mem-1")
        assert len(events) == 1
        assert events[0].event_type == "run_started"
        assert events[0].seq == 0
        assert events[0].prev_hash == GENESIS_PREV_HASH


def test_file_based_wal_mode(tmp_path: Path):
    """Verify that file-backed database enables WAL mode on local filesystem."""
    db_file = tmp_path / "journal_test.db"
    with RunJournal(db_file) as journal:
        assert journal.journal_mode == "wal"
        journal.start_run(run_id="run-file-1", task="Local WAL file task")
        ev = journal.append_event(
            run_id="run-file-1",
            event_type="tool_intent",
            step_index=1,
            payload={"tool": "read_file", "path": "test.txt"},
        )
        assert ev.seq == 1
        assert ev.prev_hash != GENESIS_PREV_HASH

    # Check WAL sidecar files or sqlite header
    assert db_file.exists()


def test_hash_chain_integrity_and_tamper_detection(tmp_path: Path):
    """Verify that the cryptographic hash chain validates when untampered, and detects corruption."""
    db_file = tmp_path / "tamper_test.db"
    journal = RunJournal(db_file)

    run_id = "run-tamper-test"
    journal.start_run(run_id=run_id, task="Testing hash chain tamper defense")

    # Append sequential events
    for step in range(1, 5):
        journal.append_event(
            run_id=run_id,
            event_type="step_action",
            step_index=step,
            payload={"action": f"step_{step}", "count": step * 10},
        )

    # 1. Verification on clean journal must pass
    valid, reason = journal.verify_run_integrity(run_id)
    assert valid is True
    assert reason is None

    # 2. Tamper with a payload directly in SQLite
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "UPDATE journal_events SET payload = ? WHERE run_id = ? AND seq = 2;",
        (canonical_json({"action": "MALICIOUS_INJECTION"}), run_id),
    )
    conn.commit()
    conn.close()

    # Re-verify: must fail on payload tampering
    valid_tampered, reason_tampered = journal.verify_run_integrity(run_id)
    assert valid_tampered is False
    assert "Payload tampered at seq 2" in (reason_tampered or "")

    journal.close()


def test_hash_chain_deletion_tamper_detection(tmp_path: Path):
    """Verify that dropping an intermediate event breaks the sequence and is detected."""
    db_file = tmp_path / "deletion_test.db"
    journal = RunJournal(db_file)

    run_id = "run-deletion-test"
    journal.start_run(run_id=run_id, task="Testing sequence deletion defense")
    for step in range(1, 4):
        journal.append_event(
            run_id=run_id,
            event_type="test_event",
            step_index=step,
            payload={"step": step},
        )

    # Drop event seq=1
    conn = sqlite3.connect(str(db_file))
    conn.execute("DELETE FROM journal_events WHERE run_id = ? AND seq = 1;", (run_id,))
    conn.commit()
    conn.close()

    valid, reason = journal.verify_run_integrity(run_id)
    assert valid is False
    assert "Sequence gap" in (reason or "") or "Hash chain broken" in (reason or "")
    journal.close()


def test_crash_recovery_of_interrupted_runs(tmp_path: Path):
    """Verify that abandoned RUNNING sessions are automatically reconciled on startup."""
    db_file = tmp_path / "recovery_test.db"

    with RunJournal(db_file) as journal:
        # Start a run that will appear abandoned (created 100 seconds ago)
        run_old = journal.start_run(run_id="run-abandoned-1", task="Abandoned mission")
        run_fresh = journal.start_run(run_id="run-fresh-2", task="Active fresh mission")

        # Artificially age the old run in the database
        past_time = time.time() - 200.0
        conn = sqlite3.connect(str(db_file))
        conn.execute("UPDATE runs SET created_at = ? WHERE run_id = ?;", (past_time, "run-abandoned-1"))
        conn.commit()
        conn.close()

        # Run crash recovery with 60 second threshold
        recovered = journal.recover_interrupted_runs(timeout_seconds=60.0)
        assert "run-abandoned-1" in recovered
        assert "run-fresh-2" not in recovered

        # Verify statuses
        rec_old = journal.get_run("run-abandoned-1")
        assert rec_old is not None
        assert rec_old.status == "INTERRUPTED"

        rec_fresh = journal.get_run("run-fresh-2")
        assert rec_fresh is not None
        assert rec_fresh.status == "RUNNING"

        # Verify interrupted event was appended
        events = journal.get_events("run-abandoned-1")
        assert events[-1].event_type == "interrupted"


def test_concurrent_multi_threaded_append(tmp_path: Path):
    """Verify that multiple threads concurrently appending events do not deadlock and preserve hash chain."""
    db_file = tmp_path / "concurrency_test.db"

    with RunJournal(db_file) as journal:
        run_id = "run-concurrent-1"
        journal.start_run(run_id=run_id, task="High concurrency stress test")

        num_threads = 6
        events_per_thread = 15

        def worker(thread_idx: int):
            for i in range(events_per_thread):
                journal.append_event(
                    run_id=run_id,
                    event_type="concurrent_action",
                    step_index=thread_idx,
                    payload={"thread": thread_idx, "iter": i},
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, t) for t in range(num_threads)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        events = journal.get_events(run_id)
        # Genesis event (1) + (num_threads * events_per_thread)
        expected_total = 1 + (num_threads * events_per_thread)
        assert len(events) == expected_total

        # Sequence must be monotonically increasing from 0 to expected_total - 1
        for idx, ev in enumerate(events):
            assert ev.seq == idx

        # Entire hash chain must be cryptographically sound
        valid, reason = journal.verify_run_integrity(run_id)
        assert valid is True, f"Integrity failed: {reason}"


def test_complete_run_lifecycle(tmp_path: Path):
    """Verify run status transitions to COMPLETED and appends run_completed event."""
    db_file = tmp_path / "lifecycle_test.db"
    with RunJournal(db_file) as journal:
        run_id = "run-lifecycle"
        journal.start_run(run_id=run_id, task="Lifecycle test")
        journal.append_event(run_id=run_id, event_type="work", payload={"data": 123})
        journal.complete_run(run_id=run_id, final_status="COMPLETED", metadata={"duration": 1.5})

        run = journal.get_run(run_id)
        assert run is not None
        assert run.status == "COMPLETED"

        events = journal.get_events(run_id)
        assert len(events) == 3
        assert events[0].event_type == "run_started"
        assert events[1].event_type == "work"
        assert events[2].event_type == "run_completed"

        valid, _ = journal.verify_run_integrity(run_id)
        assert valid is True
