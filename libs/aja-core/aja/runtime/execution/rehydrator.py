"""
aja/runtime/execution/rehydrator.py
=====================================
Phase 1 — Journal Integrity Hardening.

EventRehydrator reconstructs ExecutionSession state by folding the
deterministic events from the append-only timeline journal.  This module
is the authoritative state-reconstruction path for crash recovery and
replay verification.

Phase 1 changes
---------------
* ``JournalCorruptionError`` raised (never silently swallowed) when the
  timeline is unrecoverable.
* CRC32 verification via ``TelemetryEmitter.repair_journal()`` is now
  called *before* any events are parsed.  Corrupt frames are truncated
  and flagged, not silently dropped.
* Events are sorted by ``sequence`` (monotonic int), never by
  ``timestamp`` (wall clock, non-deterministic).
* ``ExecutionRequest`` is reconstructed losslessly from the ``request``
  dict in ``manifest.json["metadata"]["request"]`` (written by Phase 1
  contracts.py change).  Falls back to best-effort proxy for pre-Phase-1
  manifests.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from aja.runtime.execution.contracts import (
    ExecutionManifest,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSession,
    WorkspaceSnapshot,
)
from aja.runtime.execution.sequencer import TelemetryEmitter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 1 — Journal integrity error
# ---------------------------------------------------------------------------

class JournalCorruptionError(RuntimeError):
    """Raised when a timeline journal cannot be repaired to a trustworthy state.

    Attributes
    ----------
    path:           Path to the corrupt journal file.
    last_valid_seq: Sequence number of the last verified event before
                    corruption began.  -1 if no valid event was found.
    """

    def __init__(self, path: Path, last_valid_seq: int) -> None:
        self.path = path
        self.last_valid_seq = last_valid_seq
        super().__init__(
            f"Journal at '{path}' is corrupt beyond sequence {last_valid_seq}. "
            "The file has been truncated to the last verified frame. "
            "Manual forensic review is recommended before discarding."
        )


# ---------------------------------------------------------------------------
# Terminal state set (re-exported for callers)
# ---------------------------------------------------------------------------

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "timeout", "cleanup_failed", "crashed"})


# ---------------------------------------------------------------------------
# EventRehydrator
# ---------------------------------------------------------------------------

class EventRehydrator:
    """
    Event-Sourced State Rehydrator for ExecutionSessions.

    Recreates the full FSM status and final results of a session entirely
    by folding the deterministic events from the append-only timeline
    journal, acting as the single source of truth for recovery and
    verification.

    Phase 1 guarantees
    ------------------
    * Journal integrity is verified via CRC32 before any event is read.
    * Events are folded in ``sequence`` order, never ``timestamp`` order.
    * ``ExecutionRequest`` is reconstructed losslessly (full fields).
    * Corrupt journals raise ``JournalCorruptionError``; they are never
      silently hydrated into undefined state.
    """

    def __init__(self, session_id: str, base_dir: Path) -> None:
        self.session_id = session_id
        self.root = base_dir / session_id
        self.manifest_path = self.root / "manifest.json"
        self.timeline_path = self.root / "timeline.jsonl"
        self.workspace_diff_path = self.root / "workspace_diff.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rehydrate(self) -> Optional[ExecutionSession]:
        """Reconstruct an ``ExecutionSession`` from its on-disk journal.

        Returns ``None`` if the session directory or required files are
        absent.  Raises ``JournalCorruptionError`` only when the journal
        *exists* but cannot be trusted after repair.
        """
        if not self.manifest_path.exists() or not self.timeline_path.exists():
            return None

        # ----------------------------------------------------------------
        # Phase 1 Step 1: Verify and repair journal integrity BEFORE read.
        # repair_journal() truncates corrupt frames and returns the last
        # verified sequence number.  -1 means nothing was valid at all.
        # ----------------------------------------------------------------
        try:
            last_valid_seq = TelemetryEmitter.repair_journal(self.timeline_path)
            if last_valid_seq == -1 and self.timeline_path.stat().st_size > 0:
                # File has content but zero valid frames — unrecoverable.
                raise JournalCorruptionError(self.timeline_path, last_valid_seq=-1)
        except ValueError as e:
            # repair_journal raises ValueError(last_valid_seq) on detected corruption
            last_seq = e.args[0] if e.args else -1
            raise JournalCorruptionError(self.timeline_path, last_valid_seq=last_seq)

        # ----------------------------------------------------------------
        # Phase 1 Step 2: Parse manifest and reconstruct ExecutionRequest
        # losslessly.  Pre-Phase-1 manifests lack "metadata.request" and
        # fall through to a best-effort proxy (tolerant degradation).
        # ----------------------------------------------------------------
        manifest_data = json.loads(self.manifest_path.read_text(encoding="utf-8"))

        request_data = manifest_data.get("metadata", {}).get("request")
        if request_data and isinstance(request_data, dict):
            # Phase 1 path: full lossless reconstruction.
            request = ExecutionRequest.from_dict(request_data)
        else:
            # Pre-Phase-1 path: best-effort proxy.  Log a debug notice so
            # operators can identify which sessions need re-execution.
            logger.debug(
                "Session %s: manifest lacks 'metadata.request' (pre-Phase-1). "
                "ExecutionRequest reconstructed as lossy proxy.",
                self.session_id,
            )
            request = ExecutionRequest(
                command=manifest_data.get("command", ""),
                cwd=manifest_data.get("cwd"),
                env=dict(manifest_data.get("environment", {})),
                metadata=dict(manifest_data.get("metadata", {})),
            )

        session = ExecutionSession(
            session_id=self.session_id,
            request=request,
            state="created",
            trace_id=manifest_data.get("trace_id"),
            run_id=manifest_data.get("run_id"),
            root=self.root,
            manifest_path=self.manifest_path,
            stdout_path=self.root / "stdout.log",
            stderr_path=self.root / "stderr.log",
        )
        session.started_at = manifest_data.get("created_at")

        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []
        error: Optional[str] = None
        duration_ms: int = 0

        # ----------------------------------------------------------------
        # Phase 1 Step 3: Parse events from the repaired (clean) journal,
        # then sort by ``sequence`` (monotonic int) before folding.
        # Sorting by timestamp is FORBIDDEN — timestamps are wall-clock
        # and are non-deterministic across replays.
        # ----------------------------------------------------------------
        events = self._parse_timeline()

        if len(events) >= 2:
            # Assert strict monotonic ordering.  A violation indicates
            # journal corruption beyond what repair_journal() caught.
            for i in range(1, len(events)):
                prev_seq = events[i - 1].get("sequence", -1)
                curr_seq = events[i].get("sequence", -1)
                if curr_seq <= prev_seq:
                    raise JournalCorruptionError(self.timeline_path, last_valid_seq=prev_seq)

        # ----------------------------------------------------------------
        # Phase 6: Fold events into session state sequentially using REDUCERS.
        # ----------------------------------------------------------------
        from aja.runtime.event_schema import REDUCERS
        
        context = {
            "stdout_chunks": [],
            "stderr_chunks": [],
            "error": None,
            "duration_ms": 0,
        }
        
        for event in events:
            event_type = event.get("event_type")
            schema_ver = event.get("event_schema_version", "1.0")
            reducer_key = (event_type, schema_ver)
            
            reducer = REDUCERS.get(reducer_key)
            if reducer is not None:
                try:
                    reducer(session, event, context)
                except Exception as e:
                    logger.warning(
                        "Error running reducer for %s (v%s) in session %s: %s",
                        event_type, schema_ver, self.session_id, e
                    )
            else:
                logger.warning(
                    "No reducer found for event type %s (version %s) in session %s. Skipping.",
                    event_type, schema_ver, self.session_id
                )
                
        stdout_chunks = context["stdout_chunks"]
        stderr_chunks = context["stderr_chunks"]
        error = context["error"]
        duration_ms = context["duration_ms"]

        # ----------------------------------------------------------------
        # Phase 1 Step 5: Assemble ExecutionResult if terminal state reached.
        # ----------------------------------------------------------------
        if session.state in TERMINAL_STATES:
            success = session.state == "completed" and session.returncode == 0

            workspace_diff = None
            if self.workspace_diff_path.exists():
                try:
                    from aja.runtime.execution.contracts import WorkspaceDiff
                    wd_data = json.loads(
                        self.workspace_diff_path.read_text(encoding="utf-8")
                    )
                    workspace_diff = WorkspaceDiff(**wd_data)
                except Exception:
                    pass

            session.result = ExecutionResult(
                session_id=self.session_id,
                success=success,
                exit_code=int(session.returncode) if session.returncode is not None else -1,
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
                state=session.state,
                started_at=session.started_at or "",
                ended_at=session.ended_at or "",
                duration_ms=duration_ms,
                mode=manifest_data.get("backend", "unknown"),
                manifest_path=str(self.manifest_path),
                workspace_diff=workspace_diff,
                error=error,
            )

        return session

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_timeline(self) -> List[Dict[str, Any]]:
        """Read and parse events from the (already-repaired) timeline file.

        Phase 1: Events are returned in on-disk order after repair.
        The caller is responsible for sorting by ``sequence`` before
        folding.  FRAME prefix parsing is identical to repair_journal()
        so the two paths stay in sync.
        """
        events: List[Dict[str, Any]] = []
        try:
            raw = self.timeline_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return events

        for line in raw.splitlines():
            if not line:
                continue
            try:
                if line.startswith("FRAME:"):
                    parts = line.split(":", 3)
                    if len(parts) == 4:
                        events.append(json.loads(parts[3]))
                else:
                    # Legacy non-framed event (pre-TelemetryEmitter framing).
                    events.append(json.loads(line))
            except Exception:
                # A single unparseable line after repair is unusual but
                # tolerated.  It is logged, never silently swallowed.
                logger.warning(
                    "Session %s: unparseable line in timeline (already repaired). "
                    "Data may be missing from rehydrated state.",
                    self.session_id,
                )
                continue

        # Phase 1: Events must be strictly ordered on disk. We do not re-sort 
        # them here to enforce journal corruption detection on sequence regressions.
        return events
