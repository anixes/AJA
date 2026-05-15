import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from agentx.gateway.base import BasePlatformAdapter, MessageEvent, MessageType
from agentx.gateway.render import MobileMDRenderer

logger = logging.getLogger(__name__)

from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler as MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)
from telegram.constants import ParseMode
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

TELEGRAM_AVAILABLE = True


class TelegramAdapter(BasePlatformAdapter):
    """
    AJA Telegram Adapter (Assistant of Joint Agents).
    Provides a resilient, mobile-optimized interface for mission management.
    """

    def __init__(self, config: Dict[str, Any] or str):
        # Handle case where only token is passed
        if isinstance(config, str):
            config = {"token": config}
        super().__init__(config)
        self.token = config.get("token")
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None
        self._queue = asyncio.Queue()
        self._last_telemetry_check = 0
        self.name = "telegram"

    async def start(self, gateway):
        if not TELEGRAM_AVAILABLE:
            print("AJA Error: python-telegram-bot not installed.")
            return

        if not self.token:
            print("AJA Error: No Telegram token provided.")
            return

        builder = Application.builder().token(self.token)
        self._app = builder.build()
        self._bot = self._app.bot

        # Register Handlers
        self._app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND, self._handle_text_message
            )
        )
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Resilient Start
        _max_connect = 5
        for attempt in range(_max_connect):
            try:
                print("Telegram: Calling initialize()...")
                await self._app.initialize()
                print("Telegram: Calling start()...")
                await self._app.start()
                print("Telegram: Calling start_polling()...")
                await self._app.updater.start_polling(drop_pending_updates=True)
                self.is_running = True
                print("AJA Telegram Gateway started successfully.")
                break
            except Exception as e:
                wait = min(2**attempt, 30)
                print(
                    f"Telegram connect attempt {attempt + 1} failed: {e}. Retrying in {wait}s..."
                )
                await asyncio.sleep(wait)

    async def stop(self):
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        self.is_running = False
        logger.info("AJA Telegram Gateway stopped.")

    async def _handle_text_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not update.message or not update.message.text:
            return

        event = MessageEvent(
            platform="telegram",
            chat_id=str(update.message.chat_id),
            user_id=str(update.message.from_user.id),
            message_type=MessageType.TEXT,
            text=update.message.text,
            message_id=str(update.message.message_id),
            raw_event=update,
        )
        logger.info(f"Received message from {event.user_id}: {event.text}")
        await self._queue.put(event)
        return event

    async def poll(self):
        """Async generator for orchestrator to consume events."""
        while True:
            event = await self._queue.get()
            yield event

    async def tail_events(self, chat_id: str):
        """
        Background task to tail LanceDB runtime events and forward to Telegram.
        """
        from agentx.memory.secretary import AJAMemory
        memory = AJAMemory()
        last_check = datetime.now(timezone.utc).isoformat()
        
        while self.is_running:
            try:
                table = memory.db.open_table("aja_runtime_events")
                # Look for events newer than last check
                events = table.search().where(f"timestamp > '{last_check}'").to_list()
                
                for ev in events:
                    if ev['kind'] == "AWAITING_APPROVAL":
                        keyboard = [
                            [
                                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{ev['target']}"),
                                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{ev['target']}"),
                            ]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        await self.send_message(chat_id, f"⚠️ APPROVAL REQUIRED: {ev['message']}", reply_markup=reply_markup)
                    else:
                        importance = "low"
                        if ev['status'] == "ERROR":
                            importance = "high"
                        elif ev['kind'] in ["MISSION_CREATED", "MISSION_DONE"]:
                            importance = "normal"
                            
                        msg = f"[{ev['status']}] {ev['message']}"
                        await self.send_notification(chat_id, msg, importance=importance)
                    last_check = ev['timestamp']
                    
            except Exception as e:
                logger.error(f"Telemetry tail error: {e}")
            
            await asyncio.sleep(2)

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        action, mission_id = data.split("_", 1)
        
        from agentx.memory.secretary import AJAMemory
        memory = AJAMemory()
        
        if action == "approve":
            # Update mission status to ACTIVE to signal GoalEngine to resume
            memory.update_mission(mission_id, {"status": "ACTIVE"})
            
            # Log approval event
            table = memory.db.open_table("aja_runtime_events")
            table.add([{
                "event_id": uuid.uuid4().hex[:8],
                "kind": "NODE_APPROVED",
                "target": mission_id,
                "status": "SUCCESS",
                "message": f"User approved mission {mission_id}",
                "command": "",
                "metadata_json": "{}",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }])
            await query.edit_message_text(text=f"✅ Mission {mission_id} Approved.")
        else:
            # Update mission status to REJECTED
            memory.update_mission(mission_id, {"status": "REJECTED"})
            
            # Log rejection event
            table = memory.db.open_table("aja_runtime_events")
            table.add([{
                "event_id": uuid.uuid4().hex[:8],
                "kind": "NODE_REJECTED",
                "target": mission_id,
                "status": "ERROR",
                "message": f"User rejected mission {mission_id}",
                "command": "",
                "metadata_json": "{}",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }])
            await query.edit_message_text(text=f"❌ Mission {mission_id} Rejected.")

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.send_message(
            str(update.message.chat_id),
            "Hello! I am AJA (Assistant of Joint Agents), your personal natural-language secretary.",
        )

    async def send_message(self, chat_id: str, text: str, **kwargs) -> Any:
        if not self._bot:
            return None

        processed_text = self._prepare_text_for_mobile(text)

        try:
            reply_markup = kwargs.get("reply_markup")
            return await self._bot.send_message(
                chat_id=chat_id,
                text=processed_text,
                parse_mode=None, 
                reply_markup=reply_markup,
                **kwargs,
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return None

    async def send_notification(
        self, chat_id: str, text: str, importance: str = "normal"
    ):
        """
        Handles importance-based delivery.
        - 'low': Progress updates, silent.
        - 'normal': Default messages.
        - 'high': Critical errors or approval requests, always with ping.
        """
        disable_notification = importance == "low"
        await self.send_message(
            chat_id, text, disable_notification=disable_notification
        )

    def _prepare_text_for_mobile(self, text: str) -> str:
        """Applies mobile-friendly formatting (e.g. table-to-bullet)."""
        return MobileMDRenderer.render(text)
