import time
import os
import signal
import sys
from datetime import datetime, timezone, timedelta
import json
import hashlib
from collections import deque

from agentx.persistence.tasks import fetch_pending_tasks, enqueue_scheduled_tasks, update_task_status
from agentx.presence.trigger_engine import evaluate_triggers
from agentx import cmd_run

# ---------------------------------------------------------------------------
# Phase 11 — Planning Layer feature flag
# Set AGENTX_PLANNING_ENABLED=1 in the environment or agentx.json to enable.
# ---------------------------------------------------------------------------
PLANNING_ENABLED: bool = os.environ.get("AGENTX_PLANNING_ENABLED", "0").strip() == "1"

# Lazy-load planning module so existing import path is never broken
_planning_available: bool = False
try:
    from agentx.planning.planner import Planner
    from agentx.planning.dag_validator import DAGValidator
    from agentx.planning.react_executor import ReActExecutor
    _planning_available = True
except ImportError:
    pass


def _run_with_planning(objective: str, task_id: int) -> bool:
    """
    Phase 11 planning path.

    1. Decompose objective → PlanGraph via LLM-backed Planner.
    2. Validate the DAG.
    3. Execute via ReActExecutor (ReAct loop + Replanner).

    Returns True on full success, False on any failure.
    Falls back to legacy cmd_run on validation error.
    """
    if not (_planning_available and PLANNING_ENABLED):
        return False

    import uuid
    from agentx.planning.planner import PlanTooComplexError
    from agentx.persistence.tasks import update_task_error

    plan_id = str(uuid.uuid4())
    log_event("PLANNING_STARTED", {"task_id": task_id, "plan_id": plan_id, "objective": objective})

    try:
        planner = Planner()
        graph = planner.decompose(objective)

        result = DAGValidator.validate(graph)
        if not result.ok:
            print(f"[Planning] DAG validation failed — falling back to cmd_run:\n  " + "\n  ".join(result.errors))
            log_event("PLANNING_VALIDATION_FAILED", {"plan_id": plan_id, "errors": result.errors})
            return False

        executor = ReActExecutor(graph=graph, plan_id=plan_id, task_id=task_id)
        success = executor.run()

        log_event("PLANNING_FINISHED", {"plan_id": plan_id, "success": success, **executor.summary()})
        return success
        
    except PlanTooComplexError as exc:
        print(f"[Planning] Plan too complex for goal '{objective[:50]}'. Requesting human refinement.")
        log_event("PLAN_TOO_COMPLEX", {"plan_id": plan_id, "task_id": task_id, "error": str(exc)})
        send_notification("PLAN_TOO_COMPLEX", {"task_id": task_id, "objective": objective, "error": str(exc)})
        update_task_error(task_id, str(exc), error_type="PERMANENT")
        return True # Return true so the fallback isn't executed! The loop should consider it handled.

    except Exception as exc:
        print(f"[Planning] Unexpected error — falling back to cmd_run: {exc}")
        log_event("PLANNING_ERROR", {"plan_id": plan_id, "error": str(exc)})
        return False

try:
    from agentx.persistence.tracker import log_event
except ImportError:
    def log_event(event, payload):
        pass

try:
    from agentx.presence.notifier import send_notification
except ImportError:
    def send_notification(e, p):
        pass

# --- Guardrail Constants ---
MAX_TASKS_PER_MINUTE = 30
DUPLICATE_N_TIMES = 5
DUPLICATE_WINDOW_SECS = 300
NO_PROGRESS_THRESHOLD = 3
CIRCUIT_BREAKER_MAX_FAILURES = 10

# --- Guardrail State ---
execution_timestamps = deque()
task_signature_times = {}  # { signature: deque([timestamps]) }
task_progression = {}      # { task_id: {"last_hash": str, "count": int} }
circuit_breaker_failures = 0
current_backoff = 0        # dynamically added to sleep interval

# Stop flag checking
def _check_stop_flag() -> bool:
    if os.path.exists(".agentx/stop_loop"):
        return True
    return False

def _trigger_circuit_breaker(reason: str):
    print(f"\n[AgentLoop] 🚨 CIRCUIT_BREAKER_TRIGGERED: {reason}")
    log_event("CIRCUIT_BREAKER_TRIGGERED", {"reason": reason})
    send_notification("CIRCUIT_BREAKER_TRIGGERED", {"reason": reason})
    with open(".agentx/stop_loop", "w") as f:
        f.write(f"Circuit breaker tripped: {reason}")

# Graceful shutdown flag
_stop_requested = False

def _signal_handler(sig, frame):
    global _stop_requested
    print("\n[AgentLoop] Graceful shutdown requested...")
    _stop_requested = True

def _get_task_signature(task: dict) -> str:
    input_str = task.get("input", "")
    return hashlib.md5(input_str.encode()).hexdigest()

def _get_task_output_hash(task: dict) -> str:
    # A proxy for task output/progression. Could be execution_key or last_error.
    data = f"{task.get('execution_key', '')}|{task.get('last_error', '')}|{task.get('status', '')}"
    return hashlib.md5(data.encode()).hexdigest()

def run_loop(interval: int = 10, max_iterations: int = -1):
    """
    Persistent agent presence loop with execution guardrails.
    Fetches tasks, executes them, and repeats.
    """
    global _stop_requested, current_backoff, circuit_breaker_failures
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print("[AgentLoop] AGENT_LOOP_STARTED")
    log_event("AGENT_LOOP_STARTED", {"interval": interval, "max_iterations": max_iterations})

    iterations = 0
    
    while True:
        if _stop_requested or _check_stop_flag():
            print("[AgentLoop] AGENT_LOOP_STOPPED (Flag or Signal)")
            log_event("AGENT_LOOP_STOPPED", {"reason": "requested"})
            break
            
        if max_iterations > 0 and iterations >= max_iterations:
            print(f"[AgentLoop] AGENT_LOOP_STOPPED (Max iterations {max_iterations} reached)")
            log_event("AGENT_LOOP_STOPPED", {"reason": "max_iterations"})
            break
            
        iterations += 1
        log_event("AGENT_LOOP_TICK", {"iteration": iterations})
        
        # Evaluate triggers
        evaluate_triggers()
        
        # Enqueue due scheduled tasks
        enqueue_scheduled_tasks()

        # Fetch tasks
        tasks = fetch_pending_tasks(limit=5)
        
        # --- Step 4: Loop backoff (no work) ---
        if not tasks:
            log_event("AGENT_LOOP_IDLE", {"iteration": iterations})
            current_backoff = min(current_backoff + 5, 60) # increase up to +60s
            time.sleep(interval + current_backoff)
            continue
            
        # Reset backoff if we have tasks
        current_backoff = 0
        now = datetime.now(timezone.utc)
        
        for task in tasks:
            if _stop_requested:
                break
                
            task_id = task.get("id")
            input_payload = json.loads(task.get("input", "{}"))
            objective = input_payload.get("objective", "")
            
            # --- Step 1: Max tasks per window ---
            # Clean old timestamps
            cutoff_1m = now - timedelta(minutes=1)
            while execution_timestamps and execution_timestamps[0] < cutoff_1m:
                execution_timestamps.popleft()
                
            if len(execution_timestamps) >= MAX_TASKS_PER_MINUTE:
                print("[AgentLoop] ⚠️ Rate limit exceeded (tasks_executed_last_minute). Backing off.")
                log_event("LOOP_BACKOFF", {"reason": "max_tasks_per_minute"})
                time.sleep(10)
                break # break to next cycle
                
            # --- Step 2: Duplicate task detection ---
            sig = _get_task_signature(task)
            if sig not in task_signature_times:
                task_signature_times[sig] = deque()
                
            cutoff_dup = now - timedelta(seconds=DUPLICATE_WINDOW_SECS)
            while task_signature_times[sig] and task_signature_times[sig][0] < cutoff_dup:
                task_signature_times[sig].popleft()
                
            if len(task_signature_times[sig]) >= DUPLICATE_N_TIMES:
                print(f"[AgentLoop] ⚠️ DUPLICATE_TASK_DETECTED: Task {task_id} repeats too frequently.")
                log_event("DUPLICATE_TASK_DETECTED", {"task_id": task_id, "signature": sig})
                update_task_status(task_id, "SKIPPED_DUPLICATE")
                circuit_breaker_failures += 1
                continue
                
            # --- Step 3: Retry storm protection ---
            retry_count = task.get("retry_count", 0)
            if retry_count > 0:
                # E.g. delay = retry_count * 5 seconds
                cooldown = min(retry_count * 5, 300)
                last_upd = datetime.fromisoformat(task.get("updated_at", now.isoformat()))
                if now < last_upd + timedelta(seconds=cooldown):
                    print(f"[AgentLoop] ⏳ RETRY_COOLDOWN_APPLIED: Task {task_id} waiting {cooldown}s")
                    log_event("RETRY_COOLDOWN_APPLIED", {"task_id": task_id, "cooldown": cooldown})
                    continue
            
            # --- Step 5: No-progress detection ---
            out_hash = _get_task_output_hash(task)
            if task_id not in task_progression:
                task_progression[task_id] = {"last_hash": out_hash, "count": 0}
            else:
                if task_progression[task_id]["last_hash"] == out_hash and retry_count > 0:
                    task_progression[task_id]["count"] += 1
                else:
                    task_progression[task_id] = {"last_hash": out_hash, "count": 0}
                    
            if task_progression[task_id]["count"] >= NO_PROGRESS_THRESHOLD:
                print(f"[AgentLoop] 🛑 TASK_STALLED: Task {task_id} made no progress after {NO_PROGRESS_THRESHOLD} retries.")
                log_event("TASK_STALLED", {"task_id": task_id})
                send_notification("TASK_STALLED", {"task_id": task_id})
                update_task_status(task_id, "FAILED_PERMANENT")
                continue
                
            # --- Step 6: Circuit breaker (check thresholds) ---
            if circuit_breaker_failures >= CIRCUIT_BREAKER_MAX_FAILURES:
                _trigger_circuit_breaker("Too many repeated failures/duplicates")
                break
                
            # --- Execution ---
            print(f"[AgentLoop] AGENT_LOOP_EXECUTING_TASK: {task_id} - {objective[:50]}")
            log_event("AGENT_LOOP_EXECUTING_TASK", {"task_id": task_id, "objective": objective})
            
            execution_timestamps.append(now)
            task_signature_times[sig].append(now)
            
            try:
                # ── Phase 11: Planning path (feature-gated) ───────────────
                _used_planning = False
                if PLANNING_ENABLED and _planning_available:
                    _used_planning = _run_with_planning(objective, task_id)

                # ── Phase 10: Legacy cmd_run path (always available) ──────
                if not _used_planning:
                    cmd_run(objective=objective, background=False, task=task)
                # If it succeeds, we could reset circuit breaker failures
                # but we don't know the sync outcome directly here unless we check DB again.
                # We'll just assume completion and let the next cycle verify.
            except Exception as e:
                print(f"[AgentLoop] Error executing task {task_id}: {e}")
                circuit_breaker_failures += 1
                current_backoff = min(current_backoff + 10, 60)
                
        # End for tasks
        time.sleep(interval + current_backoff)
