"""
agentx/observability/trace.py
==============================
Records full execution history for auditability and replay.

Wave 2 upgrade: each trace entry now carries version_id so replays
can be filtered by plan version.
"""

import json
import time
import os
from typing import Any, Dict, List, Optional

from agentx.runtime.event_bus import bus, EVENTS
from agentx.config import PROJECT_ROOT

TRACE_DIR = PROJECT_ROOT / "traces"
TRACE_DIR.mkdir(parents=True, exist_ok=True)


class TraceStore:
    """Records full execution history for auditability and replay."""

    def __init__(self):
        self.logs: List[Dict[str, Any]] = []

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
        self.logs.append(trace_entry)

        # Flush on terminal events
        if event_type in [EVENTS["NODE_SUCCESS"], EVENTS["NODE_FAILED"], EVENTS["ROLLBACK"]]:
            plan_id = getattr(node, "plan_id", "default_plan")
            self.save(plan_id)

    def save(self, plan_id: str) -> None:
        """Persist logs to disk."""
        path = TRACE_DIR / f"trace_{plan_id}.json"
        with open(path, "w") as f:
            json.dump(self.logs, f, indent=2, default=str)

    def load(self, plan_id: str) -> List[Dict[str, Any]]:
        """Load logs from disk."""
        path = TRACE_DIR / f"trace_{plan_id}.json"
        if not path.exists():
            return []
        with open(path) as f:
            return json.load(f)

    def filter_by_version(self, version_id: str) -> List[Dict[str, Any]]:
        """Return only trace entries for a specific plan version."""
        return [e for e in self.logs if e.get("version_id") == version_id]


# Global trace store
trace_store = TraceStore()

# Hook into EventBus
bus.subscribe_once(EVENTS["NODE_STARTED"], lambda n: trace_store.record(EVENTS["NODE_STARTED"], n), "trace:NODE_STARTED")
bus.subscribe_once(EVENTS["NODE_SUCCESS"], lambda n: trace_store.record(EVENTS["NODE_SUCCESS"], n), "trace:NODE_SUCCESS")
bus.subscribe_once(EVENTS["NODE_FAILED"],  lambda n: trace_store.record(EVENTS["NODE_FAILED"], n), "trace:NODE_FAILED")
bus.subscribe_once(EVENTS["ROLLBACK"],     lambda n: trace_store.record(EVENTS["ROLLBACK"], n), "trace:ROLLBACK")
bus.subscribe_once(EVENTS["REPAIR"],       lambda n: trace_store.record(EVENTS["REPAIR"], n), "trace:REPAIR")
