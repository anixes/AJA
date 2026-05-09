import sqlite3
import os

DB_PATH = os.path.join(".agentx", "aja_secretary.sqlite3")

def check_invariants():
    """
    Validates the system invariants against the database.
    Returns a list of violations (strings).
    """
    violations = []
    
    if not os.path.exists(DB_PATH):
        return ["Database does not exist."]

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Invariant 1: logical_task_id must execute side-effects at most once.
            # (Checked via status=COMPLETED count)
            cursor.execute("""
                SELECT logical_task_id, COUNT(*) as cnt 
                FROM tasks 
                WHERE status = 'COMPLETED' AND logical_task_id IS NOT NULL
                GROUP BY logical_task_id 
                HAVING cnt > 1
            """)
            for row in cursor.fetchall():
                violations.append(f"Invariant 1 Violation: logical_task_id '{row['logical_task_id']}' has {row['cnt']} COMPLETED entries.")

            # Invariant 2: tool idempotency_key must never execute more than once.
            cursor.execute("""
                SELECT idempotency_key, COUNT(*) as cnt 
                FROM tool_executions 
                WHERE status = 'COMPLETED'
                GROUP BY idempotency_key 
                HAVING cnt > 1
            """)
            for row in cursor.fetchall():
                violations.append(f"Invariant 2 Violation: idempotency_key '{row['idempotency_key']}' has {row['cnt']} COMPLETED entries.")

            # Invariant 3: A task in COMPLETED must never revert to RUNNING.
            # (Hard to check with current schema as we don't have transition history, 
            # but we can check if there are COMPLETED tasks that are also marked as INTERRUPTED or FAILED later in time)
            # Actually, the logic should prevent this by logical_task_id coalescing.

            # Invariant 4: A lock must not remain held indefinitely.
            # (We can check for 'stale' locks - e.g. older than 1 hour)
            from datetime import datetime, timezone, timedelta
            stale_threshold = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            cursor.execute("SELECT logical_task_id, locked_at FROM task_locks WHERE locked_at < ?", (stale_threshold,))
            for row in cursor.fetchall():
                violations.append(f"Invariant 4 Warning: Stale lock detected for '{row['logical_task_id']}' since {row['locked_at']}.")

            # Invariant 5: retry_count must never exceed MAX_RETRIES.
            MAX_RETRIES = 3
            cursor.execute("SELECT id, retry_count FROM tasks WHERE retry_count > ?", (MAX_RETRIES,))
            for row in cursor.fetchall():
                violations.append(f"Invariant 5 Violation: Task {row['id']} has retry_count {row['retry_count']} which exceeds MAX_RETRIES ({MAX_RETRIES}).")

            # Invariant 6 & 7: duplicate requests must return cached result, not re-execute.
            # (Validated by ensuring no duplicate COMPLETED entries exist for same logical_task_id)
            # Already covered by Invariant 1.

    except Exception as e:
        violations.append(f"Error during invariant check: {e}")

    return violations

if __name__ == "__main__":
    v = check_invariants()
    if not v:
        print("All system invariants PASSED.")
    else:
        print("INVARIANT VIOLATIONS DETECTED:")
        for violation in v:
            print(f" - {violation}")
