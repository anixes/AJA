import inspect
import logging
import threading
from typing import Callable, Dict, List, Any

class EventBus:
    """
    Small in-process pub/sub bus for runtime notifications.

    Publishing is synchronous by design for backward compatibility, but handler
    failures are isolated so one subscriber cannot block every downstream
    observer. Use reset() in tests or embedded runtimes that need a clean bus.
    """

    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
        self._subscription_keys: set[str] = set()
        self._lock = threading.RLock()
        self._logger = logging.getLogger("agentx.runtime.event_bus")

    def subscribe(self, event_type: str, handler: Callable):
        with self._lock:
            if event_type not in self.subscribers:
                self.subscribers[event_type] = []
            self.subscribers[event_type].append(handler)
        return handler

    def subscribe_once(self, event_type: str, handler: Callable, key: str):
        """Subscribe a handler once per stable key."""
        with self._lock:
            if key in self._subscription_keys:
                return handler
            self._subscription_keys.add(key)
        return self.subscribe(event_type, handler)

    def unsubscribe(self, event_type: str, handler: Callable) -> bool:
        with self._lock:
            handlers = self.subscribers.get(event_type)
            if not handlers or handler not in handlers:
                return False
            handlers.remove(handler)
            self._subscription_keys = {
                key for key in self._subscription_keys if not key.endswith(f":{id(handler)}")
            }
            if not handlers:
                del self.subscribers[event_type]
            return True

    def reset(self):
        with self._lock:
            self.subscribers.clear()
            self._subscription_keys.clear()

    def publish(self, event_type: str, payload: Any):
        with self._lock:
            handlers = list(self.subscribers.get(event_type, ()))
        for handler in handlers:
            try:
                result = handler(payload)
                if inspect.isawaitable(result):
                    if hasattr(result, "close"):
                        result.close()
                    self._logger.warning(
                        "Event handler for %s returned an awaitable during synchronous publish; "
                        "use publish_async for async handlers.",
                        event_type,
                    )
            except Exception:
                self._logger.exception("Event handler failed for %s", event_type)

    async def publish_async(self, event_type: str, payload: Any):
        with self._lock:
            handlers = list(self.subscribers.get(event_type, ()))
        for handler in handlers:
            try:
                result = handler(payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                self._logger.exception("Async event handler failed for %s", event_type)

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
