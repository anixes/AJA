import logging
import asyncio
from typing import Dict, Any, Optional
from aja.gateway.auth import is_user_authorized
from aja.gateway.base import BasePlatformAdapter, MessageEvent, MessageType

logger = logging.getLogger(__name__)

DISCORD_AVAILABLE = False
try:
    import discord
    from discord.ext import commands
    DISCORD_AVAILABLE = True
except ImportError:
    pass

class DiscordAdapter(BasePlatformAdapter):
    """
    AJA Discord Adapter (Assistant of Joint Agents).
    Provides resilient Discord guild and channel interaction.
    """

    def __init__(self, config: Dict[str, Any] or str):
        if isinstance(config, str):
            config = {"token": config}
        super().__init__(config)
        self.token = config.get("token")
        self.name = "discord"
        self._bot = None
        self._queue = asyncio.Queue()
        self.gateway = None
        self.metrics = {
            "events_received": 0,
            "events_rejected": 0,
            "messages_sent": 0,
        }

    async def start(self, gateway):
        self.gateway = gateway
        if not self.token:
            logger.warning("[DiscordAdapter] No Discord token provided. Skipping initialization.")
            return

        if not DISCORD_AVAILABLE:
            logger.warning("[DiscordAdapter] discord.py is not installed. Discord adapter running in simulated fallback.")
            self.is_running = True
            return

        intents = discord.Intents.default()
        intents.message_content = True
        self._bot = commands.Bot(command_prefix="/", intents=intents)

        @self._bot.event
        async def on_ready():
            logger.info(f"[DiscordAdapter] Logged in as {self._bot.user} ({self._bot.user.id})")

        @self._bot.event
        async def on_message(message):
            if message.author == self._bot.user:
                return

            if not is_user_authorized("discord", str(message.author.id)):
                logger.warning(
                    "[DiscordAdapter] Unauthorized event dropped (user_id=%s, channel=%s): '%s'",
                    message.author.id,
                    message.channel.id,
                    message.content,
                )
                self.metrics["events_rejected"] += 1
                try:
                    await message.reply(
                        "🚫 Access Denied. Your Discord user is not authorized to command AJA.\n"
                        f"Add your ID to the `.env` file: `DISCORD_ALLOWED_USER_IDS={message.author.id}`"
                    )
                except Exception as e:
                    logger.debug(f"[DiscordAdapter] Could not deliver denial notice: {e}")
                return

            event = MessageEvent(
                platform="discord",
                chat_id=str(message.channel.id),
                user_id=str(message.author.id),
                message_type=MessageType.TEXT,
                text=message.content,
                message_id=str(message.id),
                raw_event=message,
            )
            self.metrics["events_received"] += 1
            await self._queue.put(event)

        self.is_running = True
        # Run standard start
        asyncio.create_task(self._bot.start(self.token))
        logger.info("[DiscordAdapter] Real Discord Client thread successfully started.")

    async def stop(self):
        if self._bot and DISCORD_AVAILABLE:
            await self._bot.close()
        self.is_running = False
        logger.info("[DiscordAdapter] Stopped.")

    async def send_message(self, chat_id: str, text: str, **kwargs) -> Any:
        self.metrics["messages_sent"] += 1
        if DISCORD_AVAILABLE and self._bot:
            try:
                channel = self._bot.get_channel(int(chat_id))
                if not channel:
                    channel = await self._bot.fetch_channel(int(chat_id))
                if channel:
                    return await channel.send(text)
            except Exception as e:
                logger.error(f"[DiscordAdapter] Failed to send message to channel {chat_id}: {e}")
        
        # Fallback simulated print
        logger.info(f"[Discord Simulated Send] Channel {chat_id}: {text}")
        return {"status": "simulated", "chat_id": chat_id, "text": text}

    async def send_notification(self, chat_id: str, text: str, importance: str = "normal"):
        # For Discord notifications, high importance pings/highlights
        if importance == "high":
            await self.send_message(chat_id, f"⚠️ **URGENT**: {text}")
        else:
            await self.send_message(chat_id, text)

    async def poll(self):
        """Yield events to GatewayRunner."""
        while True:
            yield await self._queue.get()
