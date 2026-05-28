import logging
from typing import Dict, Any, Tuple, Callable

logger = logging.getLogger(__name__)

EVENT_SCHEMAS: Dict[str, Dict[str, str]] = {
    "EXECUTION_STATE_CHANGED":   {"introduced": "1.0", "current": "1.0"},
    "EXECUTION_SESSION_CRASHED": {"introduced": "1.0", "current": "1.0"},
    "EXECUTION_PROCESS_STARTED": {"introduced": "1.0", "current": "1.0"},
    "EXECUTION_PROCESS_EXITED":  {"introduced": "1.0", "current": "1.0"},
    "EXECUTION_WORKSPACE_CREATED":{"introduced": "1.0", "current": "1.0"},
    "EXECUTION_STDOUT":          {"introduced": "1.0", "current": "1.0"},
    "EXECUTION_STDERR":          {"introduced": "1.0", "current": "1.0"},
    "EXECUTION_ERROR":           {"introduced": "1.0", "current": "1.0"},
    "EXECUTION_SESSION_FINISHED": {"introduced": "1.0", "current": "1.0"},
    "EXECUTION_ACTIVITY":        {"introduced": "1.0", "current": "1.0"},
    
    # Mission Layer Events
    "MISSION_CREATED":           {"introduced": "1.0", "current": "1.0"},
    "MISSION_STATUS_CHANGED":    {"introduced": "1.0", "current": "1.0"},
    "MISSION_RUN_STARTED":       {"introduced": "1.0", "current": "1.0"},
    "MISSION_PLAN_GENERATED":    {"introduced": "1.0", "current": "1.0"},
    "MISSION_COMPLETED":         {"introduced": "1.0", "current": "1.0"},
    "EXPLORATION_STATE_UPDATED": {"introduced": "1.0", "current": "1.0"},
    
    # Scheduler Layer Events
    "SCHEDULER_JOB_REGISTERED":  {"introduced": "1.0", "current": "1.0"},
    "SCHEDULER_JOB_FIRED":       {"introduced": "1.0", "current": "1.0"},
    "SCHEDULER_JOB_COMPLETED":   {"introduced": "1.0", "current": "1.0"},
    "SCHEDULER_JOB_PAUSED":      {"introduced": "1.0", "current": "1.0"},
    "SCHEDULER_JOB_RESUMED":     {"introduced": "1.0", "current": "1.0"},
    "SCHEDULER_JOB_DELETED":     {"introduced": "1.0", "current": "1.0"},
}

# Reducers for versioned execution session rehydration
def reduce_state_changed_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    target_state = event.get("to")
    if target_state:
        session.transition_to(target_state)

def reduce_session_crashed_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    session.state = "crashed"

def reduce_process_started_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    session.pid = event.get("pid")

def reduce_process_exited_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    session.returncode = event.get("exit_code")

def reduce_workspace_created_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    workspace_data = event.get("workspace")
    if workspace_data:
        from aja.runtime.execution.contracts import WorkspaceSnapshot
        try:
            session.workspace = WorkspaceSnapshot(**workspace_data)
        except TypeError:
            filtered = {
                k: v for k, v in workspace_data.items()
                if k in WorkspaceSnapshot.__dataclass_fields__
            }
            session.workspace = WorkspaceSnapshot(**filtered)

def reduce_stdout_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    line = event.get("line", "")
    context.setdefault("stdout_chunks", []).append(line)

def reduce_stderr_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    line = event.get("line", "")
    context.setdefault("stderr_chunks", []).append(line)

def reduce_error_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    context["error"] = event.get("error")

def reduce_session_finished_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    session.ended_at = event.get("timestamp")
    context["duration_ms"] = event.get("duration_ms", 0)

def reduce_workspace_diff_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    """No-op: workspace diff is loaded separately from workspace_diff.json."""
    pass

def reduce_workspace_cleaned_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    """No-op: cleanup is a side-effect event; no state change needed."""
    pass

def reduce_workspace_diff_failed_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    pass

def reduce_workspace_cleanup_failed_v1_0(session: Any, event: Dict[str, Any], context: Dict[str, Any]) -> None:
    """Mark cleanup failure in session state."""
    # Handled by final_state setting in manager.py
    pass

REDUCERS: Dict[Tuple[str, str], Callable[[Any, Dict[str, Any], Dict[str, Any]], None]] = {
    ("EXECUTION_STATE_CHANGED", "1.0"): reduce_state_changed_v1_0,
    ("EXECUTION_SESSION_CRASHED", "1.0"): reduce_session_crashed_v1_0,
    ("EXECUTION_PROCESS_STARTED", "1.0"): reduce_process_started_v1_0,
    ("EXECUTION_PROCESS_EXITED", "1.0"): reduce_process_exited_v1_0,
    ("EXECUTION_WORKSPACE_CREATED", "1.0"): reduce_workspace_created_v1_0,
    ("EXECUTION_STDOUT", "1.0"): reduce_stdout_v1_0,
    ("EXECUTION_STDERR", "1.0"): reduce_stderr_v1_0,
    ("EXECUTION_ERROR", "1.0"): reduce_error_v1_0,
    ("EXECUTION_SESSION_FINISHED", "1.0"): reduce_session_finished_v1_0,
    ("EXECUTION_WORKSPACE_DIFF", "1.0"): reduce_workspace_diff_v1_0,
    ("EXECUTION_WORKSPACE_CLEANED", "1.0"): reduce_workspace_cleaned_v1_0,
    ("EXECUTION_WORKSPACE_DIFF_FAILED", "1.0"): reduce_workspace_diff_failed_v1_0,
    ("EXECUTION_WORKSPACE_CLEANUP_FAILED", "1.0"): reduce_workspace_cleanup_failed_v1_0,
}
