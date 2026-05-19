import asyncio
import json
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
from agentx.runtime.event_bus import bus, EVENTS

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
        self.telemetry_queue = asyncio.Queue()
        self._last_telemetry_check = 0
        self.name = "telegram"
        self.metrics: Dict[str, Any] = {
            "events_received": 0,
            "events_dequeued": 0,
            "messages_sent": 0,
            "send_failures": 0,
            "poll_retries": 0,
            "callback_handled": 0,
            "last_error": None,
            "last_error_at": None,
            "queue_lag_seconds": 0.0,
            "queue_size": 0,
        }
        self._low_priority_last_sent: Dict[str, float] = {}
        self._low_priority_last_message: Dict[str, str] = {}
        self._low_priority_min_interval_seconds = int(
            os.getenv(
                "TELEGRAM_LOW_PRIORITY_MIN_INTERVAL_SECONDS",
                os.getenv("AJA_TELEGRAM_LOW_PRIORITY_MIN_INTERVAL_SECONDS", "8"),
            )
        )

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

        # Subscribe to standard event bus to buffer events in telemetry_queue
        for event_name in EVENTS.values():
            bus.subscribe(event_name, self._make_event_handler(event_name))

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
                self.metrics["poll_retries"] += 1
                self.metrics["last_error"] = str(e)
                self.metrics["last_error_at"] = datetime.now(timezone.utc).isoformat()
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
        self.metrics["events_received"] += 1
        self.metrics["queue_size"] = self._queue.qsize() + 1
        self.metrics["queue_lag_seconds"] = 0.0
        await self._queue.put(event)
        return event

    async def poll(self):
        """Async generator for orchestrator to consume events."""
        while True:
            event = await self._queue.get()
            self.metrics["events_dequeued"] += 1
            self.metrics["queue_size"] = self._queue.qsize()
            self.metrics["queue_lag_seconds"] = self._compute_queue_lag_seconds(
                event.timestamp
            )
            yield event

    def _make_event_handler(self, event_name: str):
        loop = asyncio.get_event_loop()
        def handler(payload: dict):
            try:
                event_id = uuid.uuid4().hex[:8]
                target = payload.get("node_id", payload.get("mission_id", "system"))
                status = "INFO"
                if "FAILED" in event_name:
                    status = "ERROR"
                elif "SUCCESS" in event_name:
                    status = "SUCCESS"
                
                message = payload.get("message", str(payload))
                if event_name == EVENTS.get("PLAN_CREATED", "PLAN_CREATED"):
                    message = payload.get("plan_summary", "Plan created.")
                
                ev = {
                    "event_id": event_id,
                    "kind": event_name,
                    "target": target,
                    "status": status,
                    "message": message,
                    "command": payload.get("command", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                # Put in queue thread-safely
                loop.call_soon_threadsafe(self.telemetry_queue.put_nowait, ev)
            except Exception as e:
                logger.error(f"[TelegramAdapter] Failed to queue event {event_name}: {e}")
        return handler

    async def tail_events(self, chat_id: str):
        """
        Background task to tail in-memory Pub/Sub events and forward to Telegram.
        """
        logger.info(f"Starting event-driven telemetry bridge for chat_id: {chat_id}")
        while self.is_running:
            try:
                ev = await self.telemetry_queue.get()
                
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
                    should_emit = True
                    if importance == "low":
                        should_emit = self._should_emit_low_priority(chat_id, msg)
                    if should_emit:
                        await self.send_notification(chat_id, msg, importance=importance)
                self.telemetry_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry tail error: {e}")
                self.metrics["last_error"] = str(e)
                self.metrics["last_error_at"] = datetime.now(timezone.utc).isoformat()
                await asyncio.sleep(1)

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        action, mission_id = data.split("_", 1)
        self.metrics["callback_handled"] += 1
        allowed_user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
        callback_user_id = str(query.from_user.id) if query.from_user else ""
        if not allowed_user_id or callback_user_id != str(allowed_user_id):
            logger.critical(f"Security Alert: Unauthorized Telegram callback attempt by user {callback_user_id}")
            await query.edit_message_text(text="🚫 Unauthorized callback action.")
            return
        
        from agentx.memory.secretary import AJAMemory
        memory = AJAMemory()
        mission = memory.get_mission(mission_id)
        if not mission:
            await query.edit_message_text(
                text=f"ℹ️ Mission {mission_id} no longer exists or was already handled."
            )
            return

        status = str(mission.get("status", "")).upper()
        metadata_raw = mission.get("metadata_json") or "{}"
        try:
            metadata = json.loads(metadata_raw)
        except Exception:
            metadata = {}

        expires_at = metadata.get("approval_expires_at") or metadata.get("expires_at")
        if expires_at:
            try:
                parsed_expires_at = datetime.fromisoformat(
                    str(expires_at).replace("Z", "+00:00")
                )
                if datetime.now(timezone.utc) > parsed_expires_at:
                    await query.edit_message_text(
                        text=f"⏳ Mission {mission_id} approval has expired."
                    )
                    return
            except Exception:
                pass
        
        if action == "approve":
            if status in {"ACTIVE", "DONE", "FAILED", "REJECTED"}:
                await query.edit_message_text(
                    text=f"ℹ️ Mission {mission_id} already handled (status: {status})."
                )
                return
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
            if status in {"ACTIVE", "REJECTED", "DONE", "FAILED"}:
                await query.edit_message_text(
                    text=f"ℹ️ Mission {mission_id} already handled (status: {status})."
                )
                return
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
            result = await self._bot.send_message(
                chat_id=chat_id,
                text=processed_text,
                parse_mode=None, 
                **kwargs,
            )
            self.metrics["messages_sent"] += 1
            return result
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            self.metrics["send_failures"] += 1
            self.metrics["last_error"] = str(e)
            self.metrics["last_error_at"] = datetime.now(timezone.utc).isoformat()
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

    def _should_emit_low_priority(self, chat_id: str, msg: str) -> bool:
        now_ts = datetime.now(timezone.utc).timestamp()
        key = str(chat_id)
        last_ts = self._low_priority_last_sent.get(key, 0.0)
        last_msg = self._low_priority_last_message.get(key, "")
        if (now_ts - last_ts) < self._low_priority_min_interval_seconds:
            return False
        if msg == last_msg and (now_ts - last_ts) < (
            self._low_priority_min_interval_seconds * 3
        ):
            return False
        self._low_priority_last_sent[key] = now_ts
        self._low_priority_last_message[key] = msg
        return True

    def get_health_snapshot(self) -> Dict[str, Any]:
        return {
            "adapter": self.name,
            "is_running": self.is_running,
            **self.metrics,
        }

    def _compute_queue_lag_seconds(self, event_timestamp: Any) -> float:
        now_ts = datetime.now(timezone.utc).timestamp()
        try:
            if isinstance(event_timestamp, (int, float)):
                return max(0.0, now_ts - float(event_timestamp))
            if isinstance(event_timestamp, str):
                parsed = datetime.fromisoformat(event_timestamp.replace("Z", "+00:00"))
                return max(0.0, now_ts - parsed.timestamp())
        except Exception:
            pass
        return 0.0
