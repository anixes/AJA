import asyncio
import functools
import json
from contextvars import ContextVar
from typing import Any, Callable, Dict, Optional, TypeVar

from aja.runtime.execution.sequencer import TelemetryEmitter
import sys

T = TypeVar("T")

class ActivityContext:
    def __init__(
        self,
        is_replay: bool = False,
        emitter: Optional[TelemetryEmitter] = None,
        replay_events: Optional[list[Dict[str, Any]]] = None,
        run_id: Optional[str] = None,
    ):
        self.is_replay = is_replay
        self.emitter = emitter
        self.replay_events = replay_events or []
        self.run_id = run_id
        self._replay_index = 0
        self._activity_counters: Dict[str, int] = {}

    def activity_id_for(self, name: str, sequence: Optional[int] = None) -> str:
        if sequence is not None:
            return f"{name}_{sequence}"
        try:
            # sys._getframe(2) targets the caller of the @durable_activity wrapper
            frame = sys._getframe(2)
            frame_id = f"{frame.f_code.co_filename}:{frame.f_lineno}"
        except Exception:
            frame_id = "unknown"
            
        key = f"{name}_{frame_id}"
        seq = self._activity_counters.get(key, 0)
        self._activity_counters[key] = seq + 1
        return f"{name}_{frame_id}_{seq}"

    def next_recorded_activity(self, activity_id: str) -> Optional[Dict[str, Any]]:
        # Find next activity event in sequence
        # activity_id could be a full id (e.g. name_filename:line_seq) or just name
        target_base = activity_id.split("_")[0] if "_" in activity_id else activity_id
        
        while self._replay_index < len(self.replay_events):
            evt = self.replay_events[self._replay_index]
            self._replay_index += 1
            if evt.get("event_type") == "EXECUTION_ACTIVITY":
                evt_id = evt.get("activity_id", "")
                evt_name = evt.get("activity_name", "")
                
                # Check exact match
                if evt_id == activity_id or evt_name == activity_id:
                    return evt
                    
                # Fallback to base name/name match
                evt_base = evt_id.split("_")[0] if "_" in evt_id else evt_id
                if target_base == evt_base or target_base == evt_name:
                    return evt
        return None


_activity_ctx: ContextVar[Optional[ActivityContext]] = ContextVar("activity_ctx", default=None)

def set_activity_context(ctx: Optional[ActivityContext]) -> Any:
    return _activity_ctx.set(ctx)

def reset_activity_context(token: Any) -> None:
    _activity_ctx.reset(token)

def get_activity_context() -> Optional[ActivityContext]:
    return _activity_ctx.get()

def _safe_serialize(obj: Any) -> Any:
    try:
        if hasattr(obj, "snapshot") and callable(obj.snapshot):
            return obj.snapshot()
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()
        if type(obj).__module__ in ("builtins", "datetime", "pathlib", "enum"):
            return str(obj)
    except Exception:
        pass
    return f"<{type(obj).__name__}>"

def _safe_format_args(args: Any, kwargs: Any) -> tuple[Any, Any]:
    try:
        return [f"<{type(a).__name__}>" for a in args], {k: f"<{type(v).__name__}>" for k, v in kwargs.items()}
    except Exception:
        return "<args_error>", "<kwargs_error>"

def durable_activity(name: str):
    """
    Decorator for non-idempotent side-effects.
    Records inputs and outputs to the execution journal during live runs.
    Yields recorded results deterministically during replay runs without re-execution.
    """
    def decorator(func: Callable[..., Any]):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            ctx = _activity_ctx.get()
            
            # If no context is set, just execute normally (unmanaged)
            if not ctx:
                return await func(*args, **kwargs)

            # Replay Mode: intercept and return recorded result
            if ctx.is_replay:
                act_id = ctx.activity_id_for(name)
                recorded = ctx.next_recorded_activity(act_id)
                if not recorded:
                    raise RuntimeError(f"Replay divergence: No recorded event found for activity '{name}' (id: {act_id}).")
                
                # Check for errors in recorded execution
                if "error" in recorded:
                    raise RuntimeError(f"Recorded activity '{name}' failed: {recorded['error']}")
                    
                result_val = recorded.get("result")
                if isinstance(result_val, dict) and "success" in result_val and "exit_code" in result_val:
                    from aja.runtime.execution.contracts import ExecutionResult
                    if "workspace_diff" in result_val and result_val["workspace_diff"] is not None:
                        from aja.runtime.execution.contracts import WorkspaceDiff
                        result_val["workspace_diff"] = WorkspaceDiff(**result_val["workspace_diff"])
                    return ExecutionResult(**result_val)
                return result_val

            # Live Mode: Execute and record
            try:
                result = await func(*args, **kwargs)
                
                # Try to serialize inputs/outputs for the journal
                try:
                    serializable_result = result.to_dict() if hasattr(result, "to_dict") else result
                    import dataclasses
                    if dataclasses.is_dataclass(serializable_result):
                        serializable_result = dataclasses.asdict(serializable_result)
                        
                    safe_args = json.loads(json.dumps(args, default=_safe_serialize))
                    safe_kwargs = json.loads(json.dumps(kwargs, default=_safe_serialize))
                    safe_result = json.loads(json.dumps(serializable_result, default=_safe_serialize))
                except Exception:
                    s_args, s_kwargs = _safe_format_args(args, kwargs)
                    safe_args, safe_kwargs, safe_result = s_args, s_kwargs, f"<{type(result).__name__}>"
                
                if ctx.emitter:
                    ctx.emitter.emit("EXECUTION_ACTIVITY", {
                        "activity_name": name,
                        "activity_id": ctx.activity_id_for(name),
                        "args": safe_args,
                        "kwargs": safe_kwargs,
                        "result": safe_result,
                        "status": "success"
                    })
                return result
                
            except Exception as e:
                if ctx.emitter:
                    ctx.emitter.emit("EXECUTION_ACTIVITY", {
                        "activity_name": name,
                        "activity_id": ctx.activity_id_for(name),
                        "error": str(e),
                        "status": "error"
                    })
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            ctx = _activity_ctx.get()
            
            if not ctx:
                return func(*args, **kwargs)

            if ctx.is_replay:
                act_id = ctx.activity_id_for(name)
                recorded = ctx.next_recorded_activity(act_id)
                if not recorded:
                    raise RuntimeError(f"Replay divergence: No recorded event found for activity '{name}' (id: {act_id}).")
                if "error" in recorded:
                    raise RuntimeError(f"Recorded activity '{name}' failed: {recorded['error']}")
                
                result_val = recorded.get("result")
                if isinstance(result_val, dict) and "success" in result_val and "exit_code" in result_val:
                    from aja.runtime.execution.contracts import ExecutionResult
                    if "workspace_diff" in result_val and result_val["workspace_diff"] is not None:
                        from aja.runtime.execution.contracts import WorkspaceDiff
                        result_val["workspace_diff"] = WorkspaceDiff(**result_val["workspace_diff"])
                    return ExecutionResult(**result_val)
                return result_val

            try:
                result = func(*args, **kwargs)
                try:
                    serializable_result = result.to_dict() if hasattr(result, "to_dict") else result
                    import dataclasses
                    if dataclasses.is_dataclass(serializable_result):
                        serializable_result = dataclasses.asdict(serializable_result)
                        
                    safe_args = json.loads(json.dumps(args, default=_safe_serialize))
                    safe_kwargs = json.loads(json.dumps(kwargs, default=_safe_serialize))
                    safe_result = json.loads(json.dumps(serializable_result, default=_safe_serialize))
                except Exception:
                    s_args, s_kwargs = _safe_format_args(args, kwargs)
                    safe_args, safe_kwargs, safe_result = s_args, s_kwargs, f"<{type(result).__name__}>"
                
                if ctx.emitter:
                    ctx.emitter.emit("EXECUTION_ACTIVITY", {
                        "activity_name": name,
                        "activity_id": ctx.activity_id_for(name),
                        "args": safe_args,
                        "kwargs": safe_kwargs,
                        "result": safe_result,
                        "status": "success"
                    })
                return result
                
            except Exception as e:
                if ctx.emitter:
                    ctx.emitter.emit("EXECUTION_ACTIVITY", {
                        "activity_name": name,
                        "activity_id": ctx.activity_id_for(name),
                        "error": str(e),
                        "status": "error"
                    })
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
