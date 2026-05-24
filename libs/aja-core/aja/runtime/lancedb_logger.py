from aja.runtime.event_bus import bus, EVENTS
from aja.runtime.events import LanceRuntimeEventSink, RuntimeEventSink

class LanceDBLogger:
    def __init__(self, event_sink: RuntimeEventSink | None = None):
        self.event_sink = event_sink or LanceRuntimeEventSink()
        self._setup_subscriptions()

    def _setup_subscriptions(self):
        # Subscribe to all standard events
        for event_name in EVENTS.values():
            bus.subscribe_once(
                event_name,
                self._log_event(event_name),
                key=f"lancedb_logger:{event_name}",
            )

    def _log_event(self, event_kind: str):
        def handler(payload: dict):
            message = payload.get("message", str(payload))
            if event_kind == EVENTS["PLAN_CREATED"]:
                message = payload.get("plan_summary", "Plan created.")

            self.event_sink.emit({
                "event_type": event_kind,
                "tool": payload.get("node_id", payload.get("mission_id", "event_bus")),
                "message": message,
                "level": "error" if "FAILED" in event_kind else "info",
                "metadata": payload,
            })
        
        return handler

# Singleton instance
lancedb_logger = LanceDBLogger()
