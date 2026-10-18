"""
run_journal.py - Durable, Tamper-Evident SQLite WAL Run Journal for AJA.
========================================================================

Provides crash-resilient event sourcing, transactional state logging,
and cryptographic hash-chain verification for agent runs.

Architecture:
- High-concurrency SQLite with WAL (Write-Ahead Logging) and busy timeout.
- Automatic fallback to DELETE mode on unsupported filesystems (e.g., UNC shares).
- Immutable, hash-chained JournalEvent sequence with SHA-256 tamper detection.
- Startup crash recovery: identifies and reconciles abandoned RUNNING sessions.
- In-process thread-safe writer synchronization via re-entrant locks.
- Zero external dependencies: pure Python standard library.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from aja.security.atomic_write import atomic_write

logger = logging.getLogger(__name__)

GENESIS_PREV_HASH = "0" * 64


def canonical_json(obj: Any) -> str:
    """Format any JSON-serializable structure deterministically for hashing."""
    return json.dumps(obj or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    """Compute SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JournalEvent:
    """Immutable audit record within a run's event sequence."""

    seq: int
    timestamp: float
    run_id: str
    event_type: str
    step_index: int
    payload: Dict[str, Any]
    payload_hash: str
    prev_hash: str
    event_hash: str


@dataclass
class RunRecord:
    """Metadata describing an active or historical agent execution run."""

    run_id: str
    created_at: float
    task: str
    status: str
    metadata: Dict[str, Any] = field(default_factory=dict)


MAX_PAYLOAD_STRING_BYTES: int = 65536


def _bound_payload_strings(data: Any, max_bytes: int = MAX_PAYLOAD_STRING_BYTES) -> Any:
    """Recursively truncate unbounded strings in payload dictionaries to prevent journal bloat."""
    if isinstance(data, str):
        if len(data) > max_bytes:
            return data[:max_bytes] + f"\n[... truncated by RunJournal: {len(data) - max_bytes} bytes omitted ...]"
        return data
    if isinstance(data, dict):
        return {k: _bound_payload_strings(v, max_bytes) for k, v in data.items()}
    if isinstance(data, list):
        return [_bound_payload_strings(item, max_bytes) for item in data]
    return data


class RunJournal:
    """
    Durable, tamper-evident Run Journal with WAL persistence and crash recovery.
    """

    def __init__(
        self,
        db_path: Union[str, Path] = ":memory:",
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.db_path = str(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self._lock = threading.RLock()
        self._closed = False

        # UNC path detection on Windows (WAL mode fails on network shares)
        self._is_unc = self.db_path.startswith(r"\\") or self.db_path.startswith("//")

        # Create parent directories if a file path is specified
        if self.db_path != ":memory:":
            Path(self.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self.db_path,
            timeout=busy_timeout_ms / 1000.0,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode; transactions managed explicitly
        )
        self._conn.row_factory = sqlite3.Row

        self._configure_database()
        self._initialize_schema()

    def _configure_database(self) -> None:
        """Configure SQLite pragmas with WAL or DELETE fallback."""
        cursor = self._conn.cursor()
        cursor.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")

        # WAL mode configuration with safe fallback
        if self._is_unc or self.db_path == ":memory:":
            # Memory or network shares: use default or DELETE mode
            cursor.execute("PRAGMA journal_mode = DELETE;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
            self._journal_mode = "delete"
        else:
            try:
                row = cursor.execute("PRAGMA journal_mode = WAL;").fetchone()
                mode = str(row[0]).lower() if row else ""
                if mode == "wal":
                    cursor.execute("PRAGMA synchronous = NORMAL;")
                    self._journal_mode = "wal"
                else:
                    cursor.execute("PRAGMA journal_mode = DELETE;")
                    cursor.execute("PRAGMA synchronous = FULL;")
                    self._journal_mode = "delete"
            except sqlite3.OperationalError as e:
                logger.warning("Failed to enable WAL mode (%s); falling back to DELETE mode", e)
                cursor.execute("PRAGMA journal_mode = DELETE;")
                cursor.execute("PRAGMA synchronous = FULL;")
                self._journal_mode = "delete"

    def _initialize_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS journal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seq INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id),
                    UNIQUE(run_id, seq)
                );
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_journal_run_seq ON journal_events(run_id, seq);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);"
            )

    @property
    def journal_mode(self) -> str:
        """Return the active SQLite journal mode ('wal' or 'delete')."""
        return self._journal_mode

    def start_run(
        self,
        run_id: str,
        task: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RunRecord:
        """
        Register a new run in RUNNING status and emit a genesis 'run_started' event.
        """
        now = time.time()
        meta = metadata or {}
        meta_json = canonical_json(meta)

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")
            try:
                cursor.execute(
                    """
                    INSERT INTO runs (run_id, created_at, task, status, metadata)
                    VALUES (?, ?, ?, 'RUNNING', ?);
                    """,
                    (run_id, now, task, meta_json),
                )
                cursor.execute("COMMIT;")
            except Exception:
                cursor.execute("ROLLBACK;")
                raise

        # Append initial genesis event
        self.append_event(
            run_id=run_id,
            event_type="run_started",
            step_index=0,
            payload={"task": task, "metadata": meta},
        )

        return RunRecord(
            run_id=run_id,
            created_at=now,
            task=task,
            status="RUNNING",
            metadata=meta,
        )


    def append_event(
        self,
        run_id: str,
        event_type: str,
        step_index: int = 0,
        payload: Optional[Dict[str, Any]] = None,
    ) -> JournalEvent:
        """
        Append an immutable, hash-chained event to the specified run.
        """
        now = time.time()
        p_dict = _bound_payload_strings(payload or {})
        p_json = canonical_json(p_dict)
        p_hash = sha256_text(p_json)

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")
            try:
                # Query the latest seq and event_hash for this run
                row = cursor.execute(
                    """
                    SELECT seq, event_hash FROM journal_events
                    WHERE run_id = ?
                    ORDER BY seq DESC LIMIT 1;
                    """,
                    (run_id,),
                ).fetchone()

                if row is None:
                    seq = 0
                    prev_hash = GENESIS_PREV_HASH
                else:
                    seq = int(row["seq"]) + 1
                    prev_hash = str(row["event_hash"])

                # Compute cryptographic event hash: sha256(prev_hash:seq:event_type:payload_hash)
                event_hash = sha256_text(f"{prev_hash}:{seq}:{event_type}:{p_hash}")

                cursor.execute(
                    """
                    INSERT INTO journal_events (
                        seq, timestamp, run_id, event_type, step_index,
                        payload, payload_hash, prev_hash, event_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        seq,
                        now,
                        run_id,
                        event_type,
                        step_index,
                        p_json,
                        p_hash,
                        prev_hash,
                        event_hash,
                    ),
                )
                cursor.execute("COMMIT;")
            except Exception:
                cursor.execute("ROLLBACK;")
                raise

        # Persist out-of-band anchor for file-backed journals
        if self.db_path != ":memory:":
            try:
                anchor_path = Path(f"{self.db_path}.head")
                anchor_content = canonical_json({
                    "run_id": run_id,
                    "seq": seq,
                    "event_hash": event_hash,
                    "timestamp": now,
                })
                atomic_write(anchor_path, anchor_content, fsync=False)
            except Exception as a_err:
                logger.debug("Failed to write external anchor: %s", a_err)

        return JournalEvent(
            seq=seq,
            timestamp=now,
            run_id=run_id,
            event_type=event_type,
            step_index=step_index,
            payload=p_dict,
            payload_hash=p_hash,
            prev_hash=prev_hash,
            event_hash=event_hash,
        )

    def complete_run(
        self,
        run_id: str,
        final_status: str = "COMPLETED",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Mark a run as completed or failed and append a final termination event.
        """
        meta = metadata or {}
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")
            try:
                cursor.execute(
                    "UPDATE runs SET status = ? WHERE run_id = ?;",
                    (final_status, run_id),
                )
                cursor.execute("COMMIT;")
            except Exception:
                cursor.execute("ROLLBACK;")
                raise

        self.append_event(
            run_id=run_id,
            event_type=f"run_{final_status.lower()}",
            payload={"status": final_status, "metadata": meta},
        )

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        """Fetch a RunRecord by ID, or None if not found."""
        with self._lock:
            cursor = self._conn.cursor()
            row = cursor.execute(
                "SELECT run_id, created_at, task, status, metadata FROM runs WHERE run_id = ?;",
                (run_id,),
            ).fetchone()

            if not row:
                return None

            try:
                meta = json.loads(row["metadata"])
            except Exception:
                meta = {}

            return RunRecord(
                run_id=row["run_id"],
                created_at=float(row["created_at"]),
                task=row["task"],
                status=row["status"],
                metadata=meta,
            )

    def get_events(self, run_id: str) -> List[JournalEvent]:
        """Retrieve all JournalEvents for a run in strict sequence order."""
        with self._lock:
            cursor = self._conn.cursor()
            rows = cursor.execute(
                """
                SELECT seq, timestamp, run_id, event_type, step_index,
                       payload, payload_hash, prev_hash, event_hash
                FROM journal_events
                WHERE run_id = ?
                ORDER BY seq ASC;
                """,
                (run_id,),
            ).fetchall()

            events: List[JournalEvent] = []
            for r in rows:
                try:
                    payload = json.loads(r["payload"])
                except Exception:
                    payload = {}

                events.append(
                    JournalEvent(
                        seq=int(r["seq"]),
                        timestamp=float(r["timestamp"]),
                        run_id=r["run_id"],
                        event_type=r["event_type"],
                        step_index=int(r["step_index"]),
                        payload=payload,
                        payload_hash=r["payload_hash"],
                        prev_hash=r["prev_hash"],
                        event_hash=r["event_hash"],
                    )
                )
            return events

    def verify_run_integrity(self, run_id: str) -> Tuple[bool, Optional[str]]:
        """
        Verify the cryptographic hash-chain integrity of a run's journal.
        Returns (True, None) if completely valid, or (False, reason) if tampered.
        """
        events = self.get_events(run_id)
        if not events:
            return True, None

        expected_prev_hash = GENESIS_PREV_HASH
        for idx, ev in enumerate(events):
            if ev.seq != idx:
                return False, f"Sequence gap at index {idx}: event.seq is {ev.seq}"

            # 1. Verify prev_hash link
            if ev.prev_hash != expected_prev_hash:
                return (
                    False,
                    f"Hash chain broken at seq {ev.seq}: expected prev_hash {expected_prev_hash}, got {ev.prev_hash}",
                )

            # 2. Recompute payload_hash
            expected_payload_hash = sha256_text(canonical_json(ev.payload))
            if ev.payload_hash != expected_payload_hash:
                return (
                    False,
                    f"Payload tampered at seq {ev.seq}: expected hash {expected_payload_hash}, got {ev.payload_hash}",
                )

            # 3. Recompute event_hash
            expected_event_hash = sha256_text(
                f"{ev.prev_hash}:{ev.seq}:{ev.event_type}:{ev.payload_hash}"
            )
            if ev.event_hash != expected_event_hash:
                return (
                    False,
                    f"Event hash invalid at seq {ev.seq}: expected {expected_event_hash}, got {ev.event_hash}",
                )

            expected_prev_hash = ev.event_hash

        # 4. Verify against out-of-band anchor file if present
        if self.db_path != ":memory:":
            try:
                anchor_path = Path(f"{self.db_path}.head")
                if anchor_path.exists():
                    anchor_info = json.loads(anchor_path.read_text(encoding="utf-8"))
                    if anchor_info.get("run_id") == run_id:
                        a_seq = anchor_info.get("seq")
                        a_hash = anchor_info.get("event_hash")
                        if a_seq == events[-1].seq and a_hash != events[-1].event_hash:
                            return (
                                False,
                                f"External anchor mismatch at seq {a_seq}: anchor has {a_hash}, journal has {events[-1].event_hash}",
                            )
            except Exception as e:
                logger.debug("Failed to verify external anchor: %s", e)

        return True, None

    def recover_interrupted_runs(self, timeout_seconds: float = 60.0) -> List[str]:
        """
        Scan for abandoned runs still marked 'RUNNING' older than timeout_seconds.
        Marks them 'INTERRUPTED' and appends an interruption event.
        """
        cutoff = time.time() - timeout_seconds
        recovered: List[str] = []

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")
            try:
                rows = cursor.execute(
                    """
                    SELECT run_id FROM runs
                    WHERE status = 'RUNNING' AND created_at < ?;
                    """,
                    (cutoff,),
                ).fetchall()

                run_ids = [r["run_id"] for r in rows]
                for rid in run_ids:
                    cursor.execute(
                        "UPDATE runs SET status = 'INTERRUPTED' WHERE run_id = ?;",
                        (rid,),
                    )
                cursor.execute("COMMIT;")
            except Exception:
                cursor.execute("ROLLBACK;")
                raise

        # Append interruption event outside of runs table transaction
        for rid in run_ids:
            self.append_event(
                run_id=rid,
                event_type="interrupted",
                payload={"reason": "abandoned_or_crashed_on_startup"},
            )
            recovered.append(rid)

        return recovered

    def checkpoint(self, truncate: bool = False) -> None:
        """Trigger an explicit SQLite WAL checkpoint."""
        with self._lock:
            if self._journal_mode == "wal":
                op = "TRUNCATE" if truncate else "PASSIVE"
                try:
                    self._conn.execute(f"PRAGMA wal_checkpoint({op});")
                except sqlite3.OperationalError as e:
                    logger.debug("WAL checkpoint skipped or failed: %s", e)

    def close(self) -> None:
        """Close SQLite connection cleanly."""
        with self._lock:
            if not self._closed:
                try:
                    self.checkpoint(truncate=True)
                    self._conn.close()
                except Exception as e:
                    logger.debug("Error during journal close: %s", e)
                finally:
                    self._closed = True

    def __enter__(self) -> RunJournal:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
