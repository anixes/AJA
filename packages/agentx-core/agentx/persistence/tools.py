"""
agentx/persistence/tools.py
Tool-level idempotency guard for AgentX.

Provides atomic reservation, failure classification, result caching, 
task-level locking, and TTL-based cleanup.

Usage pattern for tool implementors:
    from agentx.persistence.tools import ToolGuard

    guard = ToolGuard(run_id="abc123", tool_name="send_email", args={"to": "..."})
    cached = guard.reserve()
    if cached is not None:
        return cached["result"]          # coalesce: return stored result
    try:
        result = actually_send_email(...)
        guard.complete(result)
        return result
    except PermanentError as e:
        guard.fail(str(e), error_type="PERMANENT")
        raise
    except RetryableError as e:
        guard.fail(str(e), error_type="RETRYABLE")
        raise
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta
import os

DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))
_CLEANUP_TTL_DAYS = 30  # entries older than this are pruned


# ─────────────────────────────────────────────────────────────
# DB Initialisation
# ─────────────────────────────────────────────────────────────

def _init_tool_db(conn: sqlite3.Connection):
    """Create tables and indices if they don't exist (called inside an open connection)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_executions (
            idempotency_key TEXT PRIMARY KEY,
            tool_name       TEXT NOT NULL,
            args_hash       TEXT NOT NULL,
            status          TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','FAILED_RETRYABLE','FAILED_PERMANENT')),
            result          TEXT,
            error_type      TEXT,
            last_error      TEXT,
            created_at      TIMESTAMP NOT NULL,
            finished_at     TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_locks (
            logical_task_id TEXT PRIMARY KEY,
            locked_at       TIMESTAMP NOT NULL,
            lock_holder     TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_created ON tool_executions (created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_name    ON tool_executions (tool_name)")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")   # concurrent reader safety
    conn.row_factory = sqlite3.Row
    _init_tool_db(conn)
    return conn


# ─────────────────────────────────────────────────────────────
# ToolGuard — high-level API
# ─────────────────────────────────────────────────────────────

class ToolGuard:
    """
    One instance per tool call.  Wraps atomic reservation + result caching.

    Args:
        run_id    : UUID from cmd_run (orchestrator context)
        tool_name : string identifier for the tool (e.g. "send_email")
        args      : dict of call arguments — used to build the args_hash
        step      : optional sub-step name within the tool (default "main")
    """

    def __init__(self, run_id: str, tool_name: str, args: dict, step: str = "main"):
        self.tool_name = tool_name
        self.step = step
        args_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True).encode()
        ).hexdigest()
        self.args_hash = args_hash
        # Idempotency key is derived from orchestrator context, NOT from model output
        self.idempotency_key = f"{run_id}:{tool_name}:{step}:{args_hash}"

    # ── Step 1 & 3: Atomic reservation + coalesce cached result ──────────
    def reserve(self) -> dict | None:
        """
        Atomically attempt to reserve this tool call.

        Returns:
            None        → reservation succeeded; caller must execute and call complete()/fail()
            dict        → {"result": <cached_result>} if already COMPLETED (coalesce)
            dict        → {"status": "RUNNING"}       if currently running elsewhere
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")

            # INSERT OR IGNORE is the atomic check-and-reserve
            conn.execute("""
                INSERT OR IGNORE INTO tool_executions
                    (idempotency_key, tool_name, args_hash, status, created_at)
                VALUES (?, ?, ?, 'RUNNING', ?)
            """, (self.idempotency_key, self.tool_name, self.args_hash, now))

            # Check what's actually in the DB now
            row = conn.execute(
                "SELECT status, result FROM tool_executions WHERE idempotency_key = ?",
                (self.idempotency_key,)
            ).fetchone()

            conn.execute("COMMIT")

            if row["status"] == "COMPLETED":
                print(f"[ToolGuard][OK] Coalescing {self.tool_name}:{self.step} -- returning cached result.")
                return {"result": row["result"]}
            if row["status"] == "RUNNING" and row["result"] is None:
                # We just inserted it → reservation is ours
                return None
            # Another concurrent reservation is RUNNING but not ours
            return {"status": row["status"]}
        except Exception as e:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            print(f"[ToolGuard] reserve() error: {e}")
            return None
        finally:
            # Note: row might be None if exception occurred before fetch
            if 'row' in locals() and row:
                print(f"[ToolGuard][{self.idempotency_key}] Reserve check: {row['status']}")
            conn.close()

    # ── Step 3: Persist result ────────────────────────────────────────────
    def complete(self, result):
        """Mark this tool call COMPLETED and cache the result."""
        now = datetime.now(timezone.utc).isoformat()
        result_str = result if isinstance(result, str) else json.dumps(result)
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    UPDATE tool_executions
                    SET status='COMPLETED', result=?, finished_at=?
                    WHERE idempotency_key=?
                """, (result_str, now, self.idempotency_key))
        except Exception as e:
            print(f"[ToolGuard] complete() error: {e}")
        else:
            print(f"[ToolGuard][{self.idempotency_key}] Completed successfully.")

    # ── Step 2: Failure classification ───────────────────────────────────
    def fail(self, error: str, error_type: str = "RETRYABLE"):
        """
        Mark this tool call failed.

        Args:
            error      : human-readable error string
            error_type : "RETRYABLE" (default) or "PERMANENT"
        """
        if error_type not in ("RETRYABLE", "PERMANENT"):
            error_type = "RETRYABLE"
        status = f"FAILED_{error_type}"
        now = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    UPDATE tool_executions
                    SET status=?, error_type=?, last_error=?, finished_at=?
                    WHERE idempotency_key=?
                """, (status, error_type, error, now, self.idempotency_key))
        except Exception as e:
            print(f"[ToolGuard] fail() error: {e}")
        else:
            print(f"[ToolGuard][{self.idempotency_key}] Failed ({error_type}): {error}")

    def is_permanently_failed(self) -> bool:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT status FROM tool_executions WHERE idempotency_key=?",
                    (self.idempotency_key,)
                ).fetchone()
                return row is not None and row[0] == "FAILED_PERMANENT"
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────
# Step 4: Task-level locking
# ─────────────────────────────────────────────────────────────

def acquire_task_lock(logical_task_id: str, lock_holder: str) -> bool:
    """
    Acquire a logical task lock.  Returns True if lock was acquired, False if
    already held by another holder.  Uses INSERT OR IGNORE for atomicity.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                INSERT OR IGNORE INTO task_locks (logical_task_id, locked_at, lock_holder)
                VALUES (?, ?, ?)
            """, (logical_task_id, now, lock_holder))
            row = conn.execute(
                "SELECT lock_holder FROM task_locks WHERE logical_task_id=?",
                (logical_task_id,)
            ).fetchone()
            conn.execute("COMMIT")
            return row["lock_holder"] == lock_holder
        except Exception:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            return False
        finally:
            conn.close()
    except Exception as e:
        print(f"[ToolGuard] acquire_task_lock() error: {e}")
        return False
    finally:
        if 'row' in locals() and row:
            success = row["lock_holder"] == lock_holder
            print(f"[Lock][{logical_task_id}] Acquire attempt by {lock_holder}: {'SUCCESS' if success else 'FAILED (held by ' + row['lock_holder'] + ')'}")


def release_task_lock(logical_task_id: str, lock_holder: str) -> bool:
    """Release the lock if we are the holder."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "DELETE FROM task_locks WHERE logical_task_id=? AND lock_holder=?",
                (logical_task_id, lock_holder)
            )
        return True
    except Exception as e:
        print(f"[ToolGuard] release_task_lock() error: {e}")
        return False
    finally:
        print(f"[Lock][{logical_task_id}] Release attempt by {lock_holder}")


# ─────────────────────────────────────────────────────────────
# Step 5: TTL-based cleanup
# ─────────────────────────────────────────────────────────────

def cleanup_old_entries(ttl_days: int = _CLEANUP_TTL_DAYS) -> int:
    """
    Delete tool_executions rows older than ttl_days.
    Returns the number of rows deleted.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            _init_tool_db(conn)
            cursor = conn.execute(
                "DELETE FROM tool_executions WHERE created_at < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            if deleted:
                print(f"[ToolGuard] Cleaned up {deleted} expired tool execution record(s).")
            return deleted
    except Exception as e:
        print(f"[ToolGuard] cleanup_old_entries() error: {e}")
        return 0


# ─────────────────────────────────────────────────────────────
# Convenience: error classification for task-level retries
# ─────────────────────────────────────────────────────────────

class PermanentError(Exception):
    """Raise this when a failure should NOT be retried (e.g. invalid input, auth failure)."""
    pass


class RetryableError(Exception):
    """Raise this when a failure is transient and safe to retry (e.g. network timeout)."""
    pass
