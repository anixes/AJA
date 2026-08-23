"""Universal message envelope for all AJA surfaces."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class Kind(Enum):
    TEXT = auto()
    IMAGE = auto()
    VOICE = auto()
    FILE = auto()
    CALLBACK = auto()
    COMMAND = auto()
    TYPING = auto()
    RECEIPT = auto()
    STREAM_CHUNK = auto()
    ERROR = auto()


@dataclass
class Attachment:
    kind: str                    # "image", "audio", "file", etc.
    url: Optional[str] = None
    data: Optional[bytes] = None
    mime: str = ""
    name: str = ""


@dataclass
class Widget:
    """Platform-neutral interactive element rendered natively by each adapter."""
    type: str = "button"         # "button" | "keyboard" | "panel"
    label: str = ""
    action_id: str = ""          # e.g. "perm:approve:MISSION-123", "reminder:snooze:JOB-ABC"
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Envelope:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    correlation_id: str = ""     # groups streaming chunks / callback continuations
    surface: str = "cli"         # "telegram", "discord", "whatsapp", "cli"
    chat_id: str = ""
    user_id: str = ""
    kind: Kind = Kind.TEXT
    text: Optional[str] = None   # canonical markdown
    attachments: List[Attachment] = field(default_factory=list)
    widgets: List[Widget] = field(default_factory=list)
    reply_to: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def reply(self, text: str = "", **kwargs) -> "Envelope":
        """Creates an outbound reply linked to this envelope."""
        return Envelope(
            correlation_id=self.correlation_id or self.id,
            surface=self.surface,
            chat_id=self.chat_id,
            user_id=self.user_id,
            text=text,
            reply_to=self.id,
            **kwargs,
        )

    def stream_chunk(self, text: str) -> "Envelope":
        """Creates a streaming chunk belonging to this conversation."""
        return self.reply(text=text, kind=Kind.STREAM_CHUNK, meta={"streaming": True})


@dataclass
class InboundMessage:
    """Typed inbound message from any surface, consumed by ConversationCore."""
    surface: str
    chat_id: str
    user_id: str
    text: str = ""
    attachments: List[Attachment] = field(default_factory=list)
    kind: Kind = Kind.TEXT
    raw: Any = None              # platform-native object for adapters that need it
