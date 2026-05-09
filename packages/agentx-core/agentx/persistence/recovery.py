import sqlite3
import json
from datetime import datetime, timezone
import os

DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))

def recover_tasks() -> list:
    """
    Recover tasks that were interrupted or are still pending.
    Returns a list of task dictionaries ready for reprocessing.
    """
    now = datetime.now(timezone.utc).isoformat()
    recovered_tasks = []
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            # Check if table exists first
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
            if not cursor.fetchone():
                return []
                
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # 1. Fetch tasks WHERE status = "RUNNING"
            cursor.execute("SELECT id FROM tasks WHERE status = 'RUNNING'")
            running_tasks = cursor.fetchall()
            interrupted_count = len(running_tasks)
            
            # 2. For each, update status -> "INTERRUPTED" and increment retry_count
            for row in running_tasks:
                cursor.execute(
                    "UPDATE tasks SET status = 'INTERRUPTED', retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
                    (now, row["id"])
                )
            conn.commit()
            
            MAX_RETRIES = 3
            
            # 3. Fetch tasks WHERE status IN ("PENDING", "INTERRUPTED")
            cursor.execute("SELECT * FROM tasks WHERE status IN ('PENDING', 'INTERRUPTED')")
            for row in cursor.fetchall():
                task_dict = dict(row)
                
                # Check for safe replay constraints
                if task_dict["status"] == "INTERRUPTED":
                    if task_dict.get("retry_count", 0) >= MAX_RETRIES:
                        conn.execute("UPDATE tasks SET status = 'FAILED_PERMANENT' WHERE id = ?", (task_dict["id"],))
                        print(f"[Recovery][{task_dict['id']}] Exceeded retry limit ({task_dict['retry_count']}/{MAX_RETRIES}), marking FAILED_PERMANENT.")
                        continue
                    
                    logical_task_id = task_dict.get("logical_task_id")
                    if logical_task_id:
                        # check if logical task already completed
                        cursor.execute("SELECT id FROM tasks WHERE logical_task_id = ? AND status = 'COMPLETED'", (logical_task_id,))
                        if cursor.fetchone():
                            # Mark as skipped duplicate instead of recovering
                            conn.execute("UPDATE tasks SET status = 'SKIPPED_DUPLICATE' WHERE id = ?", (task_dict["id"],))
                            print(f"[Recovery][{task_dict['id']}] Found COMPLETED logical task, marking as SKIPPED_DUPLICATE.")
                            continue
                            
                print(f"[Recovery][{task_dict['id']}] Re-queuing {task_dict['status']} task (retry={task_dict.get('retry_count', 0)})")
                recovered_tasks.append(task_dict)
                
            conn.commit()
            print(f"[Recovery] Interrupted {interrupted_count} tasks.")
            print(f"[Recovery] Recovered {len(recovered_tasks)} tasks for reprocessing.")
            
    except Exception as e:
        print(f"[Recovery] Failed to recover tasks: {e}")
        
    return recovered_tasks
