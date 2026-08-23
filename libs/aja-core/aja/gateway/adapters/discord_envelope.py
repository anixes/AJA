"""Discord surface adapter speaking universal Envelope protocol.

Thin translator between Discord natives and AJA's universal Envelope type.
All business logic lives in ConversationCore — this adapter only converts.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from aja.gateway.auth import is_user_authorized
from aja.gateway.base import BasePlatformAdapter
from aja.messaging.envelope import Attachment, Envelope, InboundMessage, Kind, Widget

logger = logging.getLogger(__name__)

DISCORD_AVAILABLE = False
try:
    import discord
    DISCORD_AVAILABLE = True
except ImportError:
    pass


@dataclass
class DiscordCapabilities:
    streaming_edit: bool = True
    buttons: bool = True
    images_in: bool = True
    markdown_parse_mode: str = "markdown"


def _envelope_from_message(message) -> Optional[InboundMessage]:
    """Converts a Discord message to InboundMessage. Returns None if not processable."""
    if message.author is None or message.author.bot:
        return None
    content = message.content or ""
    attachments: List[Attachment] = []
    for att in getattr(message, "attachments", []) or []:
        if att.content_type and att.content_type.startswith("image/"):
            attachments.append(Attachment(kind="image", url=att.url, mime=att.content_type, name=att.filename))
    kind = Kind.IMAGE if attachments else Kind.TEXT
    if not content and not attachments:
        return None
    return InboundMessage(
        surface="discord",
        chat_id=str(message.channel.id),
        user_id=str(message.author.id),
        text=content,
        attachments=attachments,
        kind=kind,
        raw=message,
    )


def _widget_to_button(widget: Widget):
    """Maps an Envelope Widget to a Discord UI button."""
    from discord import ButtonStyle
    style_map = {
        "perm:approve": ButtonStyle.green,
        "perm:reject": ButtonStyle.red,
        "reminder:snooze": ButtonStyle.gray,
    }
    prefix = widget.action_id.split(":")[0] if ":" in widget.action_id else ""
    style = style_map.get(widget.action_id.rsplit(":", 1)[0] if ":" in widget.action_id else "", ButtonStyle.primary)
    btn = discord.ui.Button(label=widget.label, style=style, custom_id=widget.action_id)
    return btn


class DiscordEnvelopeAdapter(BasePlatformAdapter):
    """Discord adapter speaking universal Envelope protocol."""

    def __init__(self, config=None):
        super().__init__(config or {})
        self.name = "discord"
        self._bot = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._stream_throttle: Dict[str, float] = {}
        self.metrics: Dict[str, Any] = {
            "events_received": 0,
            "events_rejected": 0,
            "messages_sent": 0,
            "send_failures": 0,
            "callback_handled": 0,
            "last_error": None,
            "last_error_at": None,
        }
        self._on_envelope: Optional[Callable[[InboundMessage], Awaitable[None]]] = None

    def capabilities(self) -> Dict[str, Any]:
        cap = DiscordCapabilities()
        return {"streaming_edit": cap.streaming_edit, "buttons": cap.buttons, "images_in": cap.images_in}

    async def start(self, on_envelope: Callable[[InboundMessage], Awaitable[None]]) -> None:
        """Starts Discord polling, feeding InboundMessages to the callback."""
        if not DISCORD_AVAILABLE:
            logger.warning("[DiscordEnvelope] discord.py not installed.")
            return
        token = (self.config or {}).get("token", "")
        if not token:
            logger.warning("[DiscordEnvelope] No token configured.")
            return

        import discord
        from discord.ext import commands

        self._on_envelope = on_envelope
        intents = discord.Intents.default()
        intents.message_content = True
        self._bot = commands.Bot(command_prefix="!", intents=intents)

        @self._bot.event
        async def on_message(message):
            if message.author == self._bot.user:
                return
            user_id = str(message.author.id) if message.author else ""
            if not is_user_authorized("discord", user_id):
                self.metrics["events_rejected"] += 1
                logger.info("[DiscordEnvelope] Unauthorized user %s skipped.", user_id)
                return
            msg = _envelope_from_message(message)
            if msg is not None:
                self.metrics["events_received"] += 1
                await self._queue.put(msg)

        self.is_running = True
        asyncio.create_task(self._bot.start(token))
        logger.info("[DiscordEnvelope] Started.")

    async def stop(self) -> None:
        self.is_running = False
        if self._bot and DISCORD_AVAILABLE:
            await self._bot.close()

    async def listen(self) -> AsyncIterator[Envelope]:
        while self.is_running:
            msg = await self._queue.get()
            yield msg

    async def send_envelope(self, env: Envelope) -> Optional[Any]:
        """Sends an outbound Envelope to the Discord channel."""
        if not DISCORD_AVAILABLE or not self._bot:
            logger.info("[DiscordEnvelope Simulated] %s: %s", env.chat_id, env.text)
            return None
        try:
            channel = self._bot.get_channel(int(env.chat_id))
            if not channel:
                channel = await self._bot.fetch_channel(int(env.chat_id))
            kwargs: Dict[str, Any] = {}
            if env.widgets:
                view = discord.ui.View(timeout=None)
                for w in env.widgets:
                    btn = _widget_to_button(w)
                    async def _cb(interaction):
                        await interaction.response.defer()
                    btn.callback = _cb
                    view.add_item(btn)
                kwargs["view"] = view
            result = await channel.send(env.text or "", **kwargs)
            self.metrics["messages_sent"] += 1
            return result
        except Exception as e:
            logger.error("[DiscordEnvelope] Send failed: %s", e)
            self.metrics["send_failures"] += 1
            return None

    async def send_notification(self, chat_id: str, text: str, importance: str = "normal") -> None:
        prefix = "⚠️ **URGENT**: " if importance == "high" else ""
        await self.send_envelope(
            Envelope(surface="discord", chat_id=str(chat_id), text=f"{prefix}{text}")
        )

    def get_health_snapshot(self) -> Dict[str, Any]:
        return {"adapter": self.name, "is_running": self.is_running, **self.metrics}
