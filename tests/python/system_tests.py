import os
import sys
import json
import uuid
import time
import sqlite3
import subprocess
import threading
import concurrent.futures
from pathlib import Path
from datetime import datetime, timezone

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

# Ensure we use the correct python
PYTHON = sys.executable

from agentx.persistence.tasks import init_db, DB_PATH
import importlib.util
spec = importlib.util.spec_from_file_location("agentx_file", str(PROJECT_ROOT / "agentx.py"))
agentx_file = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agentx_file)
cmd_run = agentx_file.cmd_run

def setup_clean_db():
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM tasks")
        conn.execute("DELETE FROM tool_executions")
        conn.execute("DELETE FROM task_locks")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def run_cmd(args):
    """Run agentx command via subprocess to ensure clean process separation."""
    return subprocess.run([PYTHON, "agentx.py"] + args, capture_output=True, text=True)

def test_invariant_1_and_7():
    """Invariant 1 & 7: Same logical_task_id execute at most once and return cached/skipped."""
    print("\n[TEST] Invariant 1 & 7: Logical Task Idempotency")
    setup_clean_db()
    objective = "test: success"
    
    # Run 1
    print("  -> Running Mission 1...")
    res1 = run_cmd(["run", objective])
    if "Transitioned to COMPLETED" not in res1.stdout:
        print("STDOUT:", res1.stdout)
        print("STDERR:", res1.stderr)
    assert "Transitioned to COMPLETED" in res1.stdout
    
    # Run 2 (duplicate)
    print("  -> Running Duplicate Mission...")
    res2 = run_cmd(["run", objective])
    assert "Logical task already completed" in res2.stdout
    assert "Transitioned to SKIPPED_DUPLICATE" in res2.stdout
    
    print("[PASS] Invariant 1 & 7")

def test_invariant_4_concurrency_lock():
    """Invariant 4: Task-level locking prevents parallel collision."""
    print("\n[TEST] Invariant 4: Task-level Locking")
    setup_clean_db()
    objective = "test: wait" # test: wait takes 5 seconds
    
    def run_async():
        return run_cmd(["run", objective])

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        # Start first
        f1 = executor.submit(run_async)
        time.sleep(1) # Ensure it acquires lock
        
        # Start second
        f2 = executor.submit(run_async)
        
        res1 = f1.result()
        res2 = f2.result()

    # One should have finished successfully, the other should have been blocked by lock
    all_output = res1.stdout + res2.stdout
    assert "is locked by another execution" in all_output
    assert "Transitioned to SKIPPED_DUPLICATE" in all_output
    assert "Transitioned to COMPLETED" in all_output
    
    print("[PASS] Invariant 4")

def test_invariant_6_recovery():
    """Invariant 6: Recovery resumes interrupted tasks."""
    print("\n[TEST] Invariant 6: Recovery Integrity")
    setup_clean_db()
    
    # 1. Create a task and set it to RUNNING (simulate crash)
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO tasks (input, status, created_at, updated_at, logical_task_id) VALUES (?, ?, ?, ?, ?)",
        (json.dumps({"input": "test: success"}), "RUNNING", now, now, "test-logical-id")
    )
    conn.commit()
    conn.close()
    
    # 2. Run recovery (via 'doctor' or just starting agentx run)
    print("  -> Running Recovery...")
    # 'agentx run' automatically runs recovery on start
    res = run_cmd(["run", "test: dummy"]) # This will trigger recovery
    
    assert "Recovered 1 tasks" in res.stdout
    
    # 3. Verify status in DB
    conn = get_db_connection()
    row = conn.execute("SELECT status, retry_count FROM tasks WHERE logical_task_id = 'test-logical-id'").fetchone()
    # It should be COMPLETED now because recovery re-queued it and it ran
    assert row["status"] == "COMPLETED"
    assert row["retry_count"] == 1
    conn.close()
    
    print("[PASS] Invariant 6")

def test_invariant_2_tool_idempotency():
    """Invariant 2: Tool idempotency_key prevents duplicate execution."""
    print("\n[TEST] Invariant 2: Tool Idempotency")
    setup_clean_db()
    
    # This is tricky to test via CLI alone. 
    # We will run a mission that takes time, and try to run the tool manually with same run_id.
    run_id = str(uuid.uuid4())
    objective = "test: wait"
    
    def run_mission():
        # Manually invoke swarm_engine to control run_id
        cmd = [PYTHON, "scripts/swarm_engine.py", "--mode", "baton", "--objective", objective, "--run-id", run_id]
        return subprocess.run(cmd, capture_output=True, text=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(run_mission)
        time.sleep(2) # Wait for tool to start and reserve 'RUNNING'
        
        # Now try to run the tool manually with SAME run_id and SAME args
        # The tool logic should see it's already RUNNING.
        tool_cmd = [PYTHON, "scripts/test_idempotent_tool.py", run_id, "wait"]
        res_tool = subprocess.run(tool_cmd, capture_output=True, text=True)
        
        f1.result()

    # The manual tool run should NOT have logged "EXECUTING: wait" but something else (or just exited)
    # Actually my ToolGuard returns {"status": "RUNNING"} which test_idempotent_tool.py prints as COALESCE or just exits.
    assert "EXECUTING: wait" not in res_tool.stdout
    
    print("[PASS] Invariant 2")

def test_invariant_5_retry_limit():
    """Invariant 5: retry_count never exceeds MAX_RETRIES."""
    print("\n[TEST] Invariant 5: Retry Limits")
    setup_clean_db()
    
    # Create task with retry_count = 3
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO tasks (input, status, created_at, updated_at, retry_count, logical_task_id) VALUES (?, ?, ?, ?, ?, ?)",
        (json.dumps({"input": "test: fail_retryable"}), "INTERRUPTED", now, now, 3, "test-retry-limit")
    )
    conn.commit()
    conn.close()
    
    # Run recovery
    res = run_cmd(["run", "test: dummy"])
    
    assert "Exceeded retry limit (3/3)" in res.stdout
    
    # Verify status is FAILED_PERMANENT
    conn = get_db_connection()
    row = conn.execute("SELECT status FROM tasks WHERE logical_task_id = 'test-retry-limit'").fetchone()
    assert row["status"] == "FAILED_PERMANENT"
    conn.close()
    
    print("[PASS] Invariant 5")

if __name__ == "__main__":
    print("=== AgentX Resilient Recovery System Validation ===")
    try:
        test_invariant_1_and_7()
        test_invariant_4_concurrency_lock()
        test_invariant_6_recovery()
        test_invariant_2_tool_idempotency()
        test_invariant_5_retry_limit()
        print("\nALL INVARIANTS VALIDATED SUCCESSFULLY.")
    except Exception as e:
        print(f"\n[FAILURE] Validation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
