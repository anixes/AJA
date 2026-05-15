import uuid
import json
from datetime import datetime, timezone
from agentx.runtime.event_bus import bus, EVENTS
from agentx.memory.secretary import AJAMemory

class LanceDBLogger:
    def __init__(self):
        self.memory = AJAMemory()
        self._setup_subscriptions()

    def _setup_subscriptions(self):
        # Subscribe to all standard events
        for event_name in EVENTS.values():
            bus.subscribe(event_name, self._log_event(event_name))

    def _log_event(self, event_kind: str):
        def handler(payload: dict):
            try:
                table = self.memory.db.open_table("aja_runtime_events")
                event_id = uuid.uuid4().hex[:8]
                
                # Extract common fields
                target = payload.get("node_id", payload.get("mission_id", "system"))
                status = "INFO"
                if "FAILED" in event_kind:
                    status = "ERROR"
                elif "SUCCESS" in event_kind:
                    status = "SUCCESS"
                
                message = payload.get("message", str(payload))
                if event_kind == EVENTS["PLAN_CREATED"]:
                    message = payload.get("plan_summary", "Plan created.")

                row = {
                    "event_id": event_id,
                    "kind": event_kind,
                    "target": target,
                    "status": status,
                    "message": message,
                    "command": payload.get("command", ""),
                    "metadata_json": json.dumps(payload),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                table.add([row])
            except Exception as e:
                # Fallback to print if DB logging fails
                print(f"[LanceDBLogger] Failed to log event {event_kind}: {e}")
        
        return handler

# Singleton instance
lancedb_logger = LanceDBLogger()
