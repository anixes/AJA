import asyncio
import logging
import os
import re
from typing import Dict, List, Optional, Any
from agentx.gateway.base import BasePlatformAdapter, MessageEvent, MessageType
from agentx.gateway.render import MobileMDRenderer

logger = logging.getLogger(__name__)

try:
    from telegram import Update, Bot
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler as TelegramMessageHandler,
        ContextTypes,
        filters,
    )
    from telegram.constants import ParseMode
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = Bot = Application = CommandHandler = TelegramMessageHandler = ContextTypes = filters = ParseMode = Any

class TelegramAdapter(BasePlatformAdapter):
    """
    AJA Telegram Adapter.
    Inspired by hermes-agent for maximum resilience and mobile-first UX.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.token = config.get("token")
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None
        self.name = "telegram"

    async def start(self):
        if not TELEGRAM_AVAILABLE:
            logger.error("python-telegram-bot not installed.")
            return

        if not self.token:
            logger.error("No Telegram token provided.")
            return

        builder = Application.builder().token(self.token)
        self._app = builder.build()
        self._bot = self._app.bot

        # Register Handlers
        self._app.add_handler(TelegramMessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_text_message
        ))
        self._app.add_handler(CommandHandler("start", self._handle_start))

        # Resilient Start
        _max_connect = 5
        for attempt in range(_max_connect):
            try:
                await self._app.initialize()
                await self._app.start()
                await self._app.updater.start_polling(drop_pending_updates=True)
                self.is_running = True
                logger.info("AJA Telegram Gateway started successfully.")
                break
            except Exception as e:
                wait = min(2 ** attempt, 30)
                logger.warning(f"Telegram connect attempt {attempt+1} failed: {e}. Retrying in {wait}s...")
                await asyncio.sleep(wait)

    async def stop(self):
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        self.is_running = False
        logger.info("AJA Telegram Gateway stopped.")

    async def _handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return

        event = MessageEvent(
            platform="telegram",
            chat_id=str(update.message.chat_id),
            user_id=str(update.message.from_user.id),
            message_type=MessageType.TEXT,
            text=update.message.text,
            message_id=str(update.message.message_id),
            raw_event=update
        )
        # Here we would route to the AgentX core
        logger.info(f"Received message from {event.user_id}: {event.text}")
        await self.send_message(event.chat_id, f"AJA: Received '{event.text}'")

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.send_message(
            str(update.message.chat_id),
            "Hello! I am AJA (Assistant of Joint Agents), your personal secretary."
        )

    async def send_message(self, chat_id: str, text: str, **kwargs) -> Any:
        if not self._bot:
            return None
        
        # Mobile Optimization (to be implemented in Task 4)
        processed_text = self._prepare_text_for_mobile(text)
        
        try:
            return await self._bot.send_message(
                chat_id=chat_id,
                text=processed_text,
                parse_mode=ParseMode.MARKDOWN_V2 if "\\" in processed_text else None,
                **kwargs
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return None

    async def send_notification(self, chat_id: str, text: str, importance: str = "normal"):
        """
        Handles importance-based delivery.
        - 'low': Progress updates, silent.
        - 'normal': Default messages.
        - 'high': Critical errors or approval requests, always with ping.
        """
        disable_notification = importance == "low"
        
        # If the user has globally silenced 'normal' notifications, handle that here
        if self.config.get("silence_normal", False) and importance == "normal":
            disable_notification = True
            
        await self.send_message(chat_id, text, disable_notification=disable_notification)

    def _prepare_text_for_mobile(self, text: str) -> str:
        """Applies mobile-friendly formatting (e.g. table-to-bullet)."""
        return MobileMDRenderer.render(text)
