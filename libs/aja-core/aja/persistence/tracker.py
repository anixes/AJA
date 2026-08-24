import json
import logging
import uuid
from datetime import datetime, timezone
from aja.memory.manager import MemoryManager, get_memory_manager

logger = logging.getLogger(__name__)

_manager = get_memory_manager()


def log_event(event_type: str, payload: dict):
    """Log a runtime event into the Arrow-backed event feed.

    Rows are written in the shared RUNTIME_EVENTS_SCHEMA shape
    (`kind`/`status`/`timestamp` columns) so gateway telemetry pollers never
    materialize NULL status cells that would crash `.upper()` handling.
    """
    try:
        table = _manager.get_table("aja_runtime_events")

        row = [
            {
                "event_id": uuid.uuid4().hex,
                "kind": event_type,
                "target": payload.get("task_id") or payload.get("plan_id") or "system",
                "status": str(payload.get("level") or "info"),
                "message": json.dumps(payload),
                "command": "",
                "metadata_json": "{}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
        table.add(row)
    except Exception as e:
        logger.error(f"[Tracker] Failed to log event: {e}")


def get_events_by_task_id(task_id: str) -> list:
    """Retrieve all events related to a specific task ID."""
    try:
        table = _manager.get_table("aja_runtime_events")
        # Full scan filtered in Arrow
        all_rows = table.to_arrow().to_pylist()
        results = []
        for row in all_rows:
            data = json.loads(row.get("message", "{}"))
            if data.get("task_id") == task_id or data.get("objective") == task_id:
                results.append(
                    {
                        "event_type": row.get("kind"),
                        "payload": data,
                        "timestamp": row.get("timestamp"),
                    }
                )
        return results
    except Exception as e:
        logger.error(f"[Tracker] Failed to retrieve events: {e}")
        return []


def init_db():
    pass  # Tables initialized in MemoryManager
