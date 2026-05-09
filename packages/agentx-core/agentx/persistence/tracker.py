import sqlite3
import json
from datetime import datetime, timezone
import os

DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)

def log_event(event_type: str, payload: dict):
    init_db()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO execution_tracking (event_type, payload, timestamp) VALUES (?, ?, ?)",
                (event_type, json.dumps(payload), datetime.now(timezone.utc).isoformat())
            )
    except Exception as e:
        print(f"[Tracker] Failed to log event: {e}")


def get_events_by_task_id(task_id: int) -> list:
    """Retrieve all events related to a specific task ID from the log."""
    init_db()
    results = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "SELECT event_type, payload, timestamp FROM execution_tracking ORDER BY id ASC"
            )
            for etype, payload, tstamp in cursor:
                data = json.loads(payload)
                if data.get("task_id") == task_id or data.get("objective") == task_id: # some logs might use objective as id or vice versa in early versions
                    results.append({
                        "event_type": etype,
                        "payload": data,
                        "timestamp": tstamp
                    })
    except Exception as e:
        print(f"[Tracker] Failed to retrieve events: {e}")
    return results
