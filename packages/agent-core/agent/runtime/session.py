import uuid
from typing import Any, Dict, List, Optional


class Session:
    """Represents a continuous user interaction state."""

    def __init__(self, user_id: str):
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
        self.history.append({"role": role, "content": content})

    def interrupt(self):
        self.is_interrupted = True
        self.is_rejected = False

    def resume(self):
        self.is_interrupted = False

    def reject(self):
        """Mark the session as rejected and unblock the executor so it can exit."""
        self.is_rejected = True
        self.is_interrupted = False     # unblock waiting loop

    async def wait_until_resumed(self):
        """Async wait until session is resumed or rejected."""
        import asyncio
        while self.is_interrupted:
            await asyncio.sleep(0.5)


class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Session] = {}

    def get_or_create(self, user_id: str) -> Session:
        if user_id not in self.sessions:
            self.sessions[user_id] = Session(user_id)
        return self.sessions[user_id]


# Global session manager instance
session_manager = SessionManager()
