"""
aja/observability/trace.py
==============================
Records full execution history for auditability and replay.

Wave 2 upgrade: each trace entry now carries version_id so replays
can be filtered by plan version.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from aja.runtime.event_bus import bus, EVENTS
from aja.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

# Overridable so parallel test workers (pytest-xdist) never share trace files.
TRACE_DIR = (
    Path(os.environ["AJA_TRACE_DIR"])
    if os.environ.get("AJA_TRACE_DIR")
    else PROJECT_ROOT / "traces"
)
TRACE_DIR.mkdir(parents=True, exist_ok=True)

# Hard cap on in-memory trace entries per plan; prevents unbounded RAM growth
# on very long missions while keeping recent history for replay.
MAX_IN_MEMORY_TRACES_PER_PLAN = 5000


class TraceStore:
    """Records full execution history for auditability and replay.

    Thread-safe: record() and save() may be invoked concurrently from EventBus
    callbacks running on different threads.
    """

    def __init__(self):
        self.logs: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._counts: Dict[str, int] = {}

    def record(
        self,
        event_type: str,
        node: Any,
        state: Optional[Dict[str, Any]] = None,
        version_id: Optional[str] = None,
    ) -> None:
        """Append an event to the trace, optionally tagged with a version_id."""
        trace_entry = {
            "node_id": getattr(node, "id", "unknown"),
            "tool": getattr(node, "tool", "unknown"),
            "event": event_type,
            "state": state or {},
            "timestamp": time.time(),
            "version_id": version_id or getattr(node, "version_id", None),
        }
        with self._lock:
            self.logs.append(trace_entry)

            # Bound memory per plan by trimming the oldest entries of the
            # largest bucket when the global buffer grows too large.
            if len(self.logs) > MAX_IN_MEMORY_TRACES_PER_PLAN:
                self._trim_locked()

        # Flush on terminal events
        if event_type in [EVENTS["NODE_SUCCESS"], EVENTS["NODE_FAILED"], EVENTS["ROLLBACK"]]:
            plan_id = getattr(node, "plan_id", "default_plan")
            self.save(plan_id)

    def _trim_locked(self) -> None:
        """Caller must hold self._lock."""
        # Drop the oldest half to amortize trim cost instead of trimming per append.
        self.logs = self.logs[len(self.logs) // 2:]

    def save(self, plan_id: str) -> None:
        """Persist logs to disk atomically (temp file + rename)."""
        path = TRACE_DIR / f"trace_{plan_id}.json"
        tmp_path = path.with_suffix(".json.tmp")
        try:
            with self._lock:
                snapshot = list(self.logs)
            with open(tmp_path, "w") as f:
                json.dump(snapshot, f, indent=2, default=str)
            os.replace(str(tmp_path), str(path))
        except OSError as e:
            logger.warning("Failed to persist trace for %s: %s", plan_id, e)
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def load(self, plan_id: str) -> List[Dict[str, Any]]:
        """Load logs from disk."""
        path = TRACE_DIR / f"trace_{plan_id}.json"
        if not path.exists():
            return []
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            logger.warning("Corrupt trace file for %s discarded: %s", plan_id, e)
            return []

    def filter_by_version(self, version_id: str) -> List[Dict[str, Any]]:
        """Return only trace entries for a specific plan version."""
        with self._lock:
            return [e for e in self.logs if e.get("version_id") == version_id]


# Global trace store
trace_store = TraceStore()

# Hook into EventBus
bus.subscribe_once(EVENTS["NODE_STARTED"], lambda n: trace_store.record(EVENTS["NODE_STARTED"], n), "trace:NODE_STARTED")
bus.subscribe_once(EVENTS["NODE_SUCCESS"], lambda n: trace_store.record(EVENTS["NODE_SUCCESS"], n), "trace:NODE_SUCCESS")
bus.subscribe_once(EVENTS["NODE_FAILED"],  lambda n: trace_store.record(EVENTS["NODE_FAILED"], n), "trace:NODE_FAILED")
bus.subscribe_once(EVENTS["ROLLBACK"],     lambda n: trace_store.record(EVENTS["ROLLBACK"], n), "trace:ROLLBACK")
bus.subscribe_once(EVENTS["REPAIR"],       lambda n: trace_store.record(EVENTS["REPAIR"], n), "trace:REPAIR")
