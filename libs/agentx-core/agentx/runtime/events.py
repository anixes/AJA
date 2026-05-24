import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Protocol

logger = logging.getLogger(__name__)


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
            from agentx.runtime.lance_stores import LanceRuntimeStore

            memory = LanceRuntimeStore()
        self.memory = memory

    def emit(self, event: Dict[str, Any]) -> Optional[str]:
        normalized = normalize_runtime_event(event)
        try:
            return self.memory.add_runtime_event(normalized)
        except Exception:
            logger.exception("Failed to emit runtime event: %s", normalized)
            return None
