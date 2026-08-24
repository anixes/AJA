import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from aja.gateway.base import BasePlatformAdapter, MessageEvent, MessageType
from aja.gateway.render import MobileMDRenderer

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
from aja.runtime.event_bus import bus, EVENTS

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
        self._poll_task: Optional[asyncio.Task] = None
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._tail_tasks: Dict[str, asyncio.Task] = {}
        self._bus_handlers: list = []
        # Inbound user commands stay unbounded: silently dropping a command is
        # worse than backpressure. Telemetry is bounded (see dispatcher).
        self._queue = asyncio.Queue()
        _TELEMETRY_QUEUE_MAXSIZE = 1000
        self.telemetry_queue: asyncio.Queue = asyncio.Queue(maxsize=_TELEMETRY_QUEUE_MAXSIZE)
        # Per-chat fan-out queues fed by the single telemetry dispatcher.
        self._chat_queues: Dict[str, asyncio.Queue] = {}
        self._last_telemetry_check = 0
        self.name = "telegram"
        self.metrics: Dict[str, Any] = {
            "events_received": 0,
            "events_dequeued": 0,
            "messages_sent": 0,
            "send_failures": 0,
            "poll_retries": 0,
            "callback_handled": 0,
            "events_rejected": 0,
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
        self.gateway = gateway
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
        # Specific command handlers must be registered before the catch-all message MessageHandler
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(
            MessageHandler(
                filters.TEXT | filters.PHOTO, self._handle_message
            )
        )
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Subscribe to standard event bus to buffer events in telemetry_queue
        for event_name in EVENTS.values():
            handler = self._make_event_handler(event_name)
            bus.subscribe(event_name, handler)
            self._bus_handlers.append((event_name, handler))

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
                # Start background polling of LanceDB events + telemetry dispatcher
                self._poll_task = asyncio.create_task(self._poll_lancedb_events())
                self._dispatcher_task = asyncio.create_task(self._dispatch_telemetry())
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
        self.is_running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None
        for chat_id, task in list(self._tail_tasks.items()):
            task.cancel()
        self._tail_tasks.clear()
        # Unsubscribe bus handlers registered in start() so stopped adapters
        # stop accumulating events.
        for event_name, handler in self._bus_handlers:
            try:
                bus.unsubscribe(event_name, handler)
            except Exception as e:
                logger.debug("Failed to unsubscribe handler for %s: %s", event_name, e)
        self._bus_handlers.clear()
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        logger.info("AJA Telegram Gateway stopped.")

    async def _handle_text_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Backward compatibility alias for test suites and scripts."""
        return await self._handle_message(update, context)

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not update.message:
            return

        # Channel posts and anonymous-admin messages carry from_user=None.
        # There is no user to address or authorize against: fail closed by
        # skipping cleanly (no denial reply) but count the rejection.
        from_user = getattr(update.message, "from_user", None)
        if from_user is None:
            logger.info(
                "Skipping Telegram message without from_user "
                "(channel post / anonymous admin), chat_id=%s",
                getattr(update.message, "chat_id", "?"),
            )
            self.metrics["events_rejected"] += 1
            return

        # Guard the chat edge: a message without resolvable chat identity
        # cannot be routed or replied to.
        if (
            getattr(update.message, "chat", None) is None
            and getattr(update.message, "chat_id", None) is None
        ):
            logger.info("Skipping Telegram message without chat context.")
            self.metrics["events_rejected"] += 1
            return

        text_content = getattr(update.message, "text", None) or getattr(update.message, "caption", None) or ""
        media_urls = []
        msg_type = MessageType.TEXT

        photos = getattr(update.message, "photo", None)
        if photos:
            msg_type = MessageType.PHOTO
            try:
                # Highest resolution photo is last in photo array
                photo = photos[-1]
                tg_file = await context.bot.get_file(photo.file_id)
                byte_array = await tg_file.download_as_bytearray()
                import base64
                b64_data = base64.b64encode(byte_array).decode("utf-8")
                data_url = f"data:image/jpeg;base64,{b64_data}"
                media_urls.append(data_url)
                if not text_content:
                    text_content = "What can you see in this image? Please analyze and describe it in detail."
            except Exception as e:
                logger.error(f"Failed to download photo from Telegram: {e}")

        if not text_content and not media_urls:
            return

        event = MessageEvent(
            platform="telegram",
            chat_id=str(update.message.chat_id),
            user_id=str(update.message.from_user.id),
            message_type=msg_type,
            text=text_content,
            media_urls=media_urls,
            message_id=str(update.message.message_id),
            raw_event=update,
        )
        logger.info(f"Received message ({msg_type.value}) from {event.user_id}: {event.text[:50]}")
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
                self._put_telemetry(ev)
            except Exception as e:
                logger.error(f"[TelegramAdapter] Failed to queue event {event_name}: {e}")
        return handler

    def _put_telemetry(self, ev: dict):
        """Bounded enqueue with drop-oldest so a stalled consumer cannot grow
        memory without limit. Approval requests are never dropped."""
        q = self.telemetry_queue
        if ev.get("kind") == "AWAITING_APPROVAL":
            # Approval events must not be silently dropped (stranded missions);
            # make room if necessary by shedding the oldest low-value event.
            if q.full():
                try:
                    dropped = q.get_nowait()
                    logger.warning("Telemetry queue full; dropped oldest event %s", dropped.get("event_id"))
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(ev)
            return
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            try:
                dropped = q.get_nowait()
                q.put_nowait(ev)
                logger.warning(
                    "Telemetry queue full; dropped event %s (%s) for %s",
                    dropped.get("event_id"), dropped.get("kind"), ev.get("kind"),
                )
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def start_tail(self, chat_id: str) -> None:
        """Registers a per-chat telemetry tail task with tracked lifecycle."""
        chat_key = str(chat_id)
        if chat_key in self._tail_tasks and not self._tail_tasks[chat_key].done():
            return
        # Ensure the shared-queue dispatcher is running so events actually
        # reach this chat's queue.
        if self.is_running and (self._dispatcher_task is None or self._dispatcher_task.done()):
            self._dispatcher_task = asyncio.create_task(self._dispatch_telemetry())
        self._chat_queues.setdefault(chat_key, asyncio.Queue(maxsize=500))
        self._tail_tasks[chat_key] = asyncio.create_task(self.tail_events(chat_key))

    async def stop_tails(self):
        for task in list(self._tail_tasks.values()):
            task.cancel()
        self._tail_tasks.clear()
        self._chat_queues.clear()

    async def _dispatch_telemetry(self):
        """Single consumer of the shared telemetry queue that fans each event
        out to every subscribed chat's queue. Without this, N competing
        consumers delivered each event to exactly ONE arbitrary chat."""
        while True:
            try:
                ev = await self.telemetry_queue.get()
                for chat_q in list(self._chat_queues.values()):
                    try:
                        chat_q.put_nowait(ev)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Chat telemetry queue full; dropping event %s", ev.get("event_id")
                        )
                self.telemetry_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry dispatcher error: {e}")
                await asyncio.sleep(1)

    async def tail_events(self, chat_id: str):
        """
        Background task to tail this chat's telemetry queue and forward to Telegram.
        Events are fanned out from the shared queue by _dispatch_telemetry().
        """
        logger.info(f"Starting event-driven telemetry bridge for chat_id: {chat_id}")
        chat_queue = self._chat_queues.setdefault(str(chat_id), asyncio.Queue(maxsize=500))
        import time
        while self.is_running:
            try:
                ev = await chat_queue.get()
                
                # Dynamic History Update: Inject significant events into conversational session history
                if hasattr(self, "gateway") and self.gateway:
                    try:
                        session = await asyncio.to_thread(
                            self.gateway.gateway_state.get_session, chat_id
                        )
                        if ev['kind'] in ["PLAN_CREATED", "MISSION_RESULT", "MISSION_DONE", "MISSION_FAILED", "NODE_FAILED", "NODE_REJECTED", "NODE_APPROVED"]:
                            system_note = f"[System Note: {ev['message']}]"
                            session["history"].append({
                                "role": "system",
                                "text": system_note,
                                "time": time.time()
                            })
                            await asyncio.to_thread(
                                self.gateway.gateway_state.update_session, chat_id, session
                            )
                    except Exception as he:
                        logger.error(f"Failed to update session history from event: {he}")

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
                    elif ev['kind'] in ["MISSION_CREATED", "MISSION_DONE", "PLAN_CREATED", "MISSION_RESULT"]:
                        importance = "normal"
                        
                    msg = f"[{ev['status']}] {ev['message']}"
                    should_emit = True
                    if importance == "low":
                        should_emit = self._should_emit_low_priority(chat_id, msg)
                    if should_emit:
                        await self.send_notification(chat_id, msg, importance=importance)
                chat_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry tail error: {e}")
                self.metrics["last_error"] = str(e)
                self.metrics["last_error_at"] = datetime.now(timezone.utc).isoformat()
                await asyncio.sleep(1)

    async def _poll_lancedb_events(self):
        """
        Polls the LanceDB aja_runtime_events table for new events in a non-blocking background loop.
        Deduplicates via a local set of recently processed event IDs.
        """
        logger.info("Starting background LanceDB event polling task.")
        seen_event_ids = set()
        
        # Pre-populate seen event IDs with existing ones so we don't dump historical events on startup
        try:
            from aja.memory.secretary import get_aja_memory
            memory = get_aja_memory()
            table = memory.db.open_table("aja_runtime_events")
            # Get recent 200 event IDs to pre-populate seen_event_ids
            existing_events = await asyncio.to_thread(table.search().limit(200).to_list)
            for ev in existing_events:
                eid = ev.get("event_id")
                if eid:
                    seen_event_ids.add(eid)
            logger.info(f"Pre-populated {len(seen_event_ids)} existing event IDs.")
        except Exception as e:
            logger.error(f"Failed to pre-populate seen event IDs: {e}")

        while self.is_running:
            try:
                from aja.memory.secretary import get_aja_memory
                memory = get_aja_memory()
                table = memory.db.open_table("aja_runtime_events")

                # Fetch recent events off the event loop (blocking disk I/O).
                events = await asyncio.to_thread(table.search().limit(100).to_list)
                
                for ev in events:
                    eid = ev.get("event_id")
                    if not eid or eid in seen_event_ids:
                        continue

                    # eid is marked seen only AFTER successful enqueue below,
                    # so a crash mid-processing retries next tick instead of
                    # permanently dropping the row.

                    try:
                        # Format as event payload
                        kind = ev.get("kind")
                        target = ev.get("target")
                        status = (ev.get("status") or "success").upper()
                        if status == "FAILED":
                            status = "ERROR"
                        elif status == "SUCCESS":
                            status = "SUCCESS"

                        metadata_raw = ev.get("metadata_json") or "{}"
                        try:
                            metadata = json.loads(metadata_raw)
                        except Exception:
                            metadata = {}

                        message = metadata.get("message") or metadata.get("plan_summary") or ev.get("message")
                        if not message:
                            message = f"Event: {kind} on target {target}"

                        payload = {
                            "event_id": eid,
                            "kind": kind,
                            "target": target,
                            "status": status,
                            "message": message,
                            "command": ev.get("command", ""),
                            "timestamp": ev.get("timestamp") or datetime.now(timezone.utc).isoformat()
                        }

                        await self.telemetry_queue.put(payload)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(f"Failed to process event {eid}: {e}")
                        continue

                    seen_event_ids.add(eid)
                    # Keep seen_event_ids bounded in size
                    if len(seen_event_ids) > 5000:
                        seen_event_ids = set(list(seen_event_ids)[-4000:])
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error polling LanceDB events: {e}")
                
            await asyncio.sleep(1)

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query is None:
            logger.info("Ignoring callback update without callback_query payload.")
            self.metrics["events_rejected"] += 1
            return
        await query.answer()

        data = query.data or ""
        if not data.startswith(("approve_", "reject_")):
            logger.warning("Ignoring malformed callback data: %r", data[:50])
            try:
                await query.edit_message_text(text="⚠️ Unrecognized action.")
            except Exception as e:
                logger.debug("Could not acknowledge malformed callback: %s", e)
            return
        action, mission_id = data.split("_", 1)
        self.metrics["callback_handled"] += 1
        # Use the same config-fallback resolution as message authorization.
        allowed_user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID") or getattr(
            __import__("aja.config", fromlist=["TELEGRAM_ALLOWED_USER_ID"]),
            "TELEGRAM_ALLOWED_USER_ID",
            "",
        )
        callback_user_id = str(query.from_user.id) if query.from_user else ""
        if not allowed_user_id or str(allowed_user_id).strip() in ("", "*") or callback_user_id != str(allowed_user_id).strip():
            logger.critical(f"Security Alert: Unauthorized Telegram callback attempt by user {callback_user_id}")
            await query.edit_message_text(text="🚫 Unauthorized callback action.")
            return

        from aja.gateway.approvals import resolve_approval

        handled, message = await resolve_approval(
            platform="telegram",
            user_id=callback_user_id,
            mission_id=mission_id,
            action=action,
        )
        await query.edit_message_text(text=message)

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = getattr(update, "message", None)
        chat_id = getattr(message, "chat_id", None)
        if chat_id is None:
            logger.info("Ignoring /start without a resolvable chat_id.")
            self.metrics["events_rejected"] += 1
            return
        await self.send_message(
            str(chat_id),
            "Hello! I am AJA (Assistant of Joint Agents), your personal natural-language secretary.",
        )

    async def send_message(self, chat_id: str, text: str, **kwargs) -> Any:
        if not self._bot:
            return None

        if text is None:
            text = ""
        processed_text = self._prepare_text_for_mobile(str(text))
        parse_mode = kwargs.pop("parse_mode", None)

        try:
            result = await self._bot.send_message(
                chat_id=chat_id,
                text=processed_text,
                parse_mode=parse_mode, 
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
        if not isinstance(text, str):
            text = str(text) if text is not None else ""
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
