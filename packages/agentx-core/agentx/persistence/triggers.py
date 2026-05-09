import sqlite3
import json
import uuid
from datetime import datetime, timezone
import os

DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))

def init_triggers_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS triggers (
                id TEXT PRIMARY KEY,
                trigger_type TEXT NOT NULL,
                condition_payload TEXT NOT NULL,
                action_payload TEXT NOT NULL,
                cooldown_seconds INTEGER DEFAULT 60,
                last_triggered_at TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        """)

def add_trigger(trigger_type: str, condition_payload: dict, action_payload: dict, cooldown_seconds: int = 60) -> str:
    init_triggers_db()
    trigger_id = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO triggers (id, trigger_type, condition_payload, action_payload, cooldown_seconds, is_active) VALUES (?, ?, ?, ?, ?, 1)",
            (trigger_id, trigger_type, json.dumps(condition_payload), json.dumps(action_payload), cooldown_seconds)
        )
    return trigger_id

def list_triggers() -> list:
    init_triggers_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM triggers").fetchall()
        return [dict(r) for r in rows]

def disable_trigger(trigger_id: str):
    init_triggers_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE triggers SET is_active = 0 WHERE id = ?", (trigger_id,))

def delete_trigger(trigger_id: str):
    init_triggers_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))

def update_trigger_time(trigger_id: str, timestamp: str):
    init_triggers_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE triggers SET last_triggered_at = ? WHERE id = ?", (timestamp, trigger_id))

def fetch_active_triggers() -> list:
    init_triggers_db()
    # SQL-level filter to prevent trigger explosion on large sets
    now_iso = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        query = """
            SELECT * FROM triggers 
            WHERE is_active = 1 
            AND (
                last_triggered_at IS NULL 
                OR datetime(last_triggered_at, '+' || cooldown_seconds || ' seconds') <= datetime(?)
            )
        """
        rows = conn.execute(query, (now_iso,)).fetchall()
        return [dict(r) for r in rows]
