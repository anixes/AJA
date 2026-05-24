import uuid
import threading
from typing import Any, Dict, List, Optional


class Session:
    """Represents a continuous user interaction state."""

    def __init__(self, user_id: str):
        self._lock = threading.RLock()
        self.session_id = str(uuid.uuid4())
        self.user_id = user_id
        self.history: List[Dict[str, Any]] = []
        self.active_plan_id: Optional[str] = None
        self.state: Dict[str, Any] = {}
        self.is_interrupted: bool = False
        self.is_rejected: bool = False          # Wave 3: set True when user rejects the plan
        self.checkpoint: Any = None             # Holds execution state when paused
        self.pending_node: Any = None           # Holds the node awaiting OITL approval

    def log_interaction(self, role: str, content: str):
        with self._lock:
            self.history.append({"role": role, "content": content})

    def interrupt(self):
        with self._lock:
            self.is_interrupted = True
            self.is_rejected = False

    def resume(self):
        with self._lock:
            self.is_interrupted = False

    def reject(self):
        """Mark the session as rejected and unblock the executor so it can exit."""
        with self._lock:
            self.is_rejected = True
            self.is_interrupted = False     # unblock waiting loop

    async def wait_until_resumed(self):
        """Async wait until session is resumed or rejected."""
        import asyncio
        while True:
            with self._lock:
                if not self.is_interrupted:
                    return
            await asyncio.sleep(0.5)


class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Session] = {}
        self._lock = threading.RLock()

    def get_or_create(self, user_id: str) -> Session:
        with self._lock:
            if user_id not in self.sessions:
                self.sessions[user_id] = Session(user_id)
            return self.sessions[user_id]

    def get(self, user_id: str) -> Optional[Session]:
        with self._lock:
            return self.sessions.get(user_id)

    def remove(self, user_id: str) -> bool:
        with self._lock:
            return self.sessions.pop(user_id, None) is not None

    def reset(self) -> None:
        with self._lock:
            self.sessions.clear()

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                user_id: {
                    "session_id": session.session_id,
                    "active_plan_id": session.active_plan_id,
                    "history_count": len(session.history),
                    "is_interrupted": session.is_interrupted,
                    "is_rejected": session.is_rejected,
                }
                for user_id, session in self.sessions.items()
            }


# Global session manager instance
session_manager = SessionManager()
