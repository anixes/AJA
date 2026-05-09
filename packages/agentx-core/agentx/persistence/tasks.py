import sqlite3
import json
from datetime import datetime, timezone
import os

DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                error_type TEXT,
                execution_hash TEXT,
                execution_started_at TIMESTAMP,
                execution_finished_at TIMESTAMP,
                run_id TEXT,
                logical_task_id TEXT,
                execution_key TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_logical ON tasks (logical_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks (status)")
        # Try to add columns if they don't exist (for existing databases)
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN retry_count INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE tasks ADD COLUMN last_error TEXT")
            conn.execute("ALTER TABLE tasks ADD COLUMN execution_hash TEXT")
            conn.execute("ALTER TABLE tasks ADD COLUMN execution_started_at TIMESTAMP")
            conn.execute("ALTER TABLE tasks ADD COLUMN execution_finished_at TIMESTAMP")
        except sqlite3.OperationalError:
            pass # Columns already exist

        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN run_id TEXT")
            conn.execute("ALTER TABLE tasks ADD COLUMN logical_task_id TEXT")
            conn.execute("ALTER TABLE tasks ADD COLUMN execution_key TEXT")
            conn.execute("ALTER TABLE tasks ADD COLUMN error_type TEXT")
        except sqlite3.OperationalError:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_payload TEXT NOT NULL,
                next_run_at TIMESTAMP NOT NULL,
                interval_seconds INTEGER,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_next ON scheduled_tasks (next_run_at)")

def is_logical_task_completed(logical_task_id: str) -> bool:
    if not logical_task_id: return False
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM tasks WHERE logical_task_id = ? AND status = 'COMPLETED'", (logical_task_id,))
            return cursor.fetchone() is not None
    except Exception:
        return False

def set_execution_metadata(task_id: int, execution_key: str = None, started_at: str = None, finished_at: str = None, run_id: str = None, logical_task_id: str = None):
    if task_id < 0: return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            if execution_key is not None:
                conn.execute("UPDATE tasks SET execution_key = ? WHERE id = ?", (execution_key, task_id))
            if started_at is not None:
                conn.execute("UPDATE tasks SET execution_started_at = ? WHERE id = ?", (started_at, task_id))
            if finished_at is not None:
                conn.execute("UPDATE tasks SET execution_finished_at = ? WHERE id = ?", (finished_at, task_id))
            if run_id is not None:
                conn.execute("UPDATE tasks SET run_id = ? WHERE id = ?", (run_id, task_id))
            if logical_task_id is not None:
                conn.execute("UPDATE tasks SET logical_task_id = ? WHERE id = ?", (logical_task_id, task_id))
    except Exception as e:
        print(f"[Tasks] Failed to update execution metadata: {e}")

def create_task(payload: dict) -> int:
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "INSERT INTO tasks (input, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (json.dumps(payload), "PENDING", now, now)
            )
            return cursor.lastrowid
    except Exception as e:
        print(f"[Tasks] Failed to create task: {e}")
        return -1
    finally:
        if 'cursor' in locals() and cursor.lastrowid:
            print(f"[Tasks][{cursor.lastrowid}] Created task: PENDING")

def update_task_status(task_id: int, status: str):
    if task_id < 0:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, task_id)
            )
    except Exception as e:
        print(f"[Tasks] Failed to update task: {e}")
    else:
        print(f"[Tasks][{task_id}] Transitioned to {status}")

def update_task_error(task_id: int, error: str, error_type: str = "RETRYABLE"):
    """
    Record error details on a task and set its status.
    error_type: 'RETRYABLE' or 'PERMANENT'
    """
    if task_id < 0:
        return
    if error_type not in ("RETRYABLE", "PERMANENT"):
        error_type = "RETRYABLE"
    status = "FAILED_PERMANENT" if error_type == "PERMANENT" else "FAILED"
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE tasks SET status=?, last_error=?, error_type=?, updated_at=? WHERE id=?",
                (status, error, error_type, now, task_id)
            )
    except Exception as e:
        print(f"[Tasks] Failed to record task error: {e}")
    else:
        print(f"[Tasks][{task_id}] Error recorded ({error_type}): {status}")

def cleanup_old_tasks(ttl_days: int = 30) -> int:
    """Delete COMPLETED / FAILED_PERMANENT tasks older than ttl_days. Returns rows deleted."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "DELETE FROM tasks WHERE status IN ('COMPLETED','FAILED_PERMANENT','SKIPPED_DUPLICATE') AND updated_at < ?",
                (cutoff,)
            )
            deleted = cursor.rowcount
            if deleted:
                print(f"[Tasks] Cleaned up {deleted} expired task record(s).")
            return deleted
    except Exception as e:
        print(f"[Tasks] cleanup_old_tasks() error: {e}")
        return 0

def fetch_pending_tasks(limit: int = 10) -> list:
    """Fetch tasks for the agent loop: INTERRUPTED first, then PENDING, then FAILED (retryable)."""
    init_db()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM tasks 
                WHERE status IN ('INTERRUPTED', 'PENDING', 'FAILED')
                ORDER BY 
                    CASE status
                        WHEN 'INTERRUPTED' THEN 1
                        WHEN 'PENDING' THEN 2
                        WHEN 'FAILED' THEN 3
                    END,
                    updated_at ASC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[Tasks] fetch_pending_tasks error: {e}")
        return []

def enqueue_scheduled_tasks():
    """Check scheduled_tasks and enqueue them into tasks if due."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            due_tasks = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE next_run_at <= ?",
                (now,)
            ).fetchall()
            
            for st in due_tasks:
                payload_str = st["task_payload"]
                
                # enqueue task
                cursor = conn.execute(
                    "INSERT INTO tasks (input, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (payload_str, "PENDING", now, now)
                )
                
                # Update next_run_at if it's recurring, else delete
                if st["interval_seconds"]:
                    import datetime as dt
                    next_run = (datetime.now(timezone.utc) + dt.timedelta(seconds=st["interval_seconds"])).isoformat()
                    conn.execute("UPDATE scheduled_tasks SET next_run_at=?, updated_at=? WHERE id=?", (next_run, now, st["id"]))
                else:
                    conn.execute("DELETE FROM scheduled_tasks WHERE id=?", (st["id"],))
                    
    except Exception as e:
        print(f"[Tasks] enqueue_scheduled_tasks error: {e}")
