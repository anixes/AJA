import abc
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


class MessageType(Enum):
    TEXT = "text"
    PHOTO = "photo"
    AUDIO = "audio"
    DOCUMENT = "document"
    COMMAND = "command"


@dataclass
class MessageEvent:
    """Unified message event for all AJA Gateway platforms."""
    platform: str
    chat_id: str
    user_id: str
    message_type: MessageType
    text: Optional[str] = None
    media_urls: List[str] = field(default_factory=list)
    media_types: List[str] = field(default_factory=list)
    raw_event: Any = None
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    reply_to_id: Optional[str] = None
    message_id: Optional[str] = None


class BasePlatformAdapter(abc.ABC):
    """Abstract base class for AJA Gateway platform adapters."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.is_running = False

    @abc.abstractmethod
    async def start(self):
        """Start the platform adapter."""
        pass

    @abc.abstractmethod
    async def stop(self):
        """Stop the platform adapter."""
        pass

    @abc.abstractmethod
    async def send_message(self, chat_id: str, text: str, **kwargs) -> Any:
        """Send a message to the platform."""
        pass

    @abc.abstractmethod
    async def send_notification(self, chat_id: str, text: str, importance: str = "normal"):
        """Send a notification (handling silence/priority logic)."""
        pass
