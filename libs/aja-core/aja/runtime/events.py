import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Protocol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical execution event-type constants.
# Every event type written to a timeline.jsonl MUST appear here.
# This is the authoritative vocabulary for journal events.
# ---------------------------------------------------------------------------
EXECUTION_EVENTS: Dict[str, str] = {
    # FSM lifecycle
    "EXECUTION_STATE_CHANGED":          "EXECUTION_STATE_CHANGED",
    # Workspace
    "EXECUTION_WORKSPACE_CREATED":      "EXECUTION_WORKSPACE_CREATED",
    "EXECUTION_WORKSPACE_DIFF":         "EXECUTION_WORKSPACE_DIFF",
    "EXECUTION_WORKSPACE_DIFF_FAILED":  "EXECUTION_WORKSPACE_DIFF_FAILED",
    "EXECUTION_WORKSPACE_CLEANED":      "EXECUTION_WORKSPACE_CLEANED",
    "EXECUTION_WORKSPACE_CLEANUP_FAILED": "EXECUTION_WORKSPACE_CLEANUP_FAILED",
    # Process
    "EXECUTION_PROCESS_STARTED":        "EXECUTION_PROCESS_STARTED",
    "EXECUTION_PROCESS_EXITED":         "EXECUTION_PROCESS_EXITED",
    # Streams
    "EXECUTION_STDOUT":                 "EXECUTION_STDOUT",
    "EXECUTION_STDERR":                 "EXECUTION_STDERR",
    # Errors
    "EXECUTION_ERROR":                  "EXECUTION_ERROR",
    # Session terminal events
    "EXECUTION_SESSION_FINISHED":       "EXECUTION_SESSION_FINISHED",
    # Phase 1: Crash-orphan detection — emitted when a session with no terminal
    # event is discovered on manager startup (process died mid-run).
    "EXECUTION_SESSION_CRASHED":        "EXECUTION_SESSION_CRASHED",
    # Durable activities (Phase 2+)
    "EXECUTION_ACTIVITY":               "EXECUTION_ACTIVITY",
}


@dataclass(frozen=True)
class RuntimeEvent:
    """Canonical runtime event envelope shared by schedulers and telemetry sinks."""

    event_type: str
    tool: str = "runtime"
    message: str = ""
    level: str = "info"
    status: str = "success"
    trace_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, event: Dict[str, Any]) -> "RuntimeEvent":
        metadata = dict(event.get("metadata") or {})
        known = {
            "event_type",
            "tool",
            "message",
            "level",
            "status",
            "trace_id",
            "run_id",
            "metadata",
        }
        for key, value in event.items():
            if key not in known:
                metadata.setdefault(key, value)
        return cls(
            event_type=str(event.get("event_type", "RUNTIME_EVENT")),
            tool=str(event.get("tool", "runtime")),
            message=str(event.get("message", "")),
            level=str(event.get("level", "info")),
            status=str(event.get("status", "success")),
            trace_id=event.get("trace_id"),
            run_id=event.get("run_id"),
            metadata=metadata,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_runtime_event(event: Dict[str, Any]) -> Dict[str, Any]:
    return RuntimeEvent.from_dict(event).to_dict()


class RuntimeEventSink(Protocol):
    """Minimal runtime event sink contract."""

    def emit(self, event: Dict[str, Any]) -> Optional[str]:
        ...


class NullRuntimeEventSink:
    """No-op sink for tests and embedded runtime use."""

    def emit(self, event: Dict[str, Any]) -> Optional[str]:
        return None


class LanceRuntimeEventSink:
    """
    LanceDB-backed event sink.

    The import is lazy so runtime schedulers can depend on this contract without
    importing the client memory store at module import time.
    """

    def __init__(self, memory: Any = None):
        if memory is None:
            from aja.runtime.lance_stores import LanceRuntimeStore

            memory = LanceRuntimeStore()
        self.memory = memory

    def emit(self, event: Dict[str, Any]) -> Optional[str]:
        normalized = normalize_runtime_event(event)
        try:
            return self.memory.add_runtime_event(normalized)
        except Exception:
            logger.exception("Failed to emit runtime event: %s", normalized)
            return None
