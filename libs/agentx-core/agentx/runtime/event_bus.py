from typing import Callable, Dict, List, Any

class EventBus:
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, handler: Callable):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)

    def publish(self, event_type: str, payload: Any):
        if event_type in self.subscribers:
            for handler in self.subscribers[event_type]:
                handler(payload)

# Global event bus for the runtime
bus = EventBus()

# Standard Event Types
EVENTS = {
    "TASK_RECEIVED":      "TASK_RECEIVED",
    "NODE_STARTED":       "NODE_STARTED",
    "NODE_SUCCESS":       "NODE_SUCCESS",
    "NODE_FAILED":        "NODE_FAILED",
    "ROLLBACK":           "ROLLBACK",
    "REPAIR":             "REPAIR",
    "PLAN_CREATED":       "PLAN_CREATED",
    # Wave 3: Operator-in-the-Loop events
    "AWAITING_APPROVAL":  "AWAITING_APPROVAL",
    "NODE_APPROVED":      "NODE_APPROVED",
    "NODE_REJECTED":      "NODE_REJECTED",
    "MISSION_RESULT":     "MISSION_RESULT",
}
