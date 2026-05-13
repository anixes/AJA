import json
from datetime import datetime, timezone
from agentx.memory.manager import MemoryManager, get_memory_manager

_manager = get_memory_manager()


def log_event(event_type: str, payload: dict):
    """Log a runtime event into the Arrow-backed event feed."""
    try:
        table = _manager.get_table("aja_runtime_events")
        import uuid

        row = [
            {
                "event_id": uuid.uuid4().hex,
                "event_type": event_type,
                "message": json.dumps(payload),
                "level": payload.get("level", "info"),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        table.add(row)
    except Exception as e:
        print(f"[Tracker] Failed to log event: {e}")


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
                        "event_type": row["event_type"],
                        "payload": data,
                        "timestamp": row["created_at"],
                    }
                )
        return results
    except Exception as e:
        print(f"[Tracker] Failed to retrieve events: {e}")
        return []


def init_db():
    pass  # Tables initialized in MemoryManager
