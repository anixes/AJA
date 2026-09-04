import asyncio
from contextlib import asynccontextmanager
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
try:
    from telegram import ReactionTypeEmoji
except ImportError:  # very old PTB
    ReactionTypeEmoji = None
from aja.runtime.event_bus import bus, EVENTS

TELEGRAM_AVAILABLE = True

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_MAX_FILE_BYTES = 20 * 1024 * 1024

ACK_REACTION_EMOJI = "👀"
DONE_REACTION_EMOJI = "✅"
DONE_REACTION_FALLBACK_EMOJI = "👍"  # ✅ is not in Telegram's allowed reaction set
ERROR_REACTION_EMOJI = "👎"
STATUS_BUBBLE_INITIAL_TEXT = "🔧 Working…"


class StatusBubble:
    """Single in-place '🔧 Working…' progress message for one turn.

    Sent silently (disable_notification=True) at turn start, edited as tools
    run, then either edited into the final answer or deleted when the final
    answer is sent as a separate message.

    NOTE: not yet wired — the turn lifecycle lives in
    UnifiedGateway.handle_gateway_event (libs/aja-core/aja/gateway/orchestrator.py).
    Wiring point: create the bubble right before intent dispatch (~L520) and
    finalize/delete around the final send_message at ~L809.
    """

    def __init__(self, bot: Any, chat_id: str):
        self._bot = bot
        self.chat_id = str(chat_id)
        self.message_id: Optional[int] = None
        self._finalized = False

    @property
    def active(self) -> bool:
        return self.message_id is not None and not self._finalized

    async def start(self) -> "StatusBubble":
        try:
            msg = await self._bot.send_message(
                chat_id=self.chat_id,
                text=STATUS_BUBBLE_INITIAL_TEXT,
                disable_notification=True,
            )
            self.message_id = getattr(msg, "message_id", None)
        except Exception as e:
            logger.debug("StatusBubble start failed (cosmetic): %s", e)
            self.message_id = None
        return self

    async def update(self, text: str) -> None:
        if not self.active:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
                disable_notification=True,
            )
        except Exception as e:
            logger.debug("StatusBubble update failed (cosmetic): %s", e)

    async def finalize(self, final_text: Optional[str] = None) -> bool:
        """Edit the bubble into the final answer, or delete it when the final
        answer is delivered by a separate send. Returns True when the final
        text was delivered via this bubble (caller should skip its own send)."""
        if not self.active:
            return False
        self._finalized = True
        try:
            if final_text:
                await self._bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=final_text,
                )
                return True
            await self._bot.delete_message(
                chat_id=self.chat_id, message_id=self.message_id
            )
        except Exception as e:
            logger.debug("StatusBubble finalize failed (cosmetic): %s", e)
        return False

    async def fail(self, error_text: str = "❌ Something went wrong.") -> None:
        await self.finalize(error_text)


async def _safe_set_reaction(bot: Any, chat_id: str, message_id: Any, emoji: str) -> bool:
    """Best-effort message reaction; never raises (groups may forbid them,
    old PTB may lack the API). Returns True if applied."""
    if bot is None or message_id is None:
        return False
    setter = getattr(bot, "set_message_reaction", None)
    if setter is None:
        logger.debug("Bot lacks set_message_reaction; skipping ack reaction.")
        return False
    try:
        if ReactionTypeEmoji is not None:
            reaction = [ReactionTypeEmoji(emoji)]
        else:
            reaction = [emoji]
        await setter(chat_id=chat_id, message_id=int(message_id), reaction=reaction)
        return True
    except Exception as e:
        logger.debug("setMessageReaction failed (cosmetic): %s", e)
        return False


@asynccontextmanager
async def continuous_chat_action(
    bot: Any,
    chat_id: Any,
    action: str = "typing",
    interval: float = 4.0,
):
    """Periodically sends chat actions (e.g. typing) to Telegram until exited.

    Telegram chat actions expire automatically after ~5 seconds. This context manager
    keeps the typing status alive in the background so the user always sees that
    the model is generating or executing tools.
    """
    if bot is None or not chat_id:
        yield
        return

    stop_event = asyncio.Event()

    async def _pulse():
        while not stop_event.is_set():
            try:
                action_sender = getattr(bot, "send_chat_action", None)
                if action_sender:
                    await action_sender(chat_id=chat_id, action=action)
            except Exception as e:
                logger.debug("ChatAction pulse failed (cosmetic): %s", e)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(_pulse())
    try:
        yield
    finally:
        stop_event.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def split_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> List[str]:
    """Split text into Telegram-sized chunks on newline/space boundaries."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else [""]
    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[: limit + 1]
        cut = max(window.rfind("\n"), window.rfind(" "))
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    return [chunk for chunk in chunks if chunk]


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

    @property
    def bot(self) -> Optional[Bot]:
        return self._bot

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
        self._app.add_handler(CommandHandler("app", self._handle_app))
        self._app.add_handler(
            MessageHandler(
                filters.TEXT
                | filters.PHOTO
                | filters.VOICE
                | filters.AUDIO
                | filters.Document.ALL
                | filters.Sticker.ALL
                | filters.LOCATION
                | filters.CONTACT,
                self._handle_message,
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
                # Fire-and-forget: menu registration must never block/fail startup.
                try:
                    from aja.gateway.telegram_menu import register_command_menu
                    asyncio.create_task(register_command_menu(self._bot))
                except Exception as e:
                    logger.debug("Menu registration scheduling failed: %s", e)
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
            photo = photos[-1]
            file_size = getattr(photo, "file_size", None) or 0
            if file_size > TELEGRAM_MAX_FILE_BYTES:
                # Telegram's getFile endpoint refuses files > 20 MiB; reject
                # up-front with a user-visible message instead of a cryptic
                # download failure.
                logger.warning(
                    "Skipping Telegram photo download: %s bytes exceeds the 20 MiB getFile cap.",
                    file_size,
                )
                try:
                    await self.send_message(
                        str(update.message.chat_id),
                        f"⚠️ Image skipped: it is too large for Telegram's file API "
                        f"({file_size / (1024 * 1024):.1f} MB > 20 MB limit). "
                        f"I'll continue with your text.",
                    )
                except Exception as notify_err:
                    logger.error("Failed to deliver oversized-photo notice: %s", notify_err)
            else:
                try:
                    # Highest resolution photo is last in photo array
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

        # Document / File Attachment (Code, Text, Configs, Logs, or Uncompressed Images)
        doc = getattr(update.message, "document", None)
        if doc:
            doc_file_name = getattr(doc, "file_name", None) or f"doc_{update.message.message_id}"
            file_size = getattr(doc, "file_size", None) or 0
            if file_size > TELEGRAM_MAX_FILE_BYTES:
                logger.warning(
                    "Skipping Telegram document download: %s bytes exceeds 20 MiB cap.",
                    file_size,
                )
                try:
                    await self.send_message(
                        str(update.message.chat_id),
                        f"⚠️ Document '{doc_file_name}' skipped: file size "
                        f"({file_size / (1024 * 1024):.1f} MB > 20 MB limit).",
                    )
                except Exception as notify_err:
                    logger.error("Failed to deliver oversized-document notice: %s", notify_err)
            else:
                try:
                    tg_file = await context.bot.get_file(doc.file_id)
                    byte_array = await tg_file.download_as_bytearray()
                    doc_mime = getattr(doc, "mime_type", "") or ""

                    # Check if uncompressed image sent as document
                    is_image = doc_mime.startswith("image/") or any(
                        doc_file_name.lower().endswith(ext)
                        for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
                    )
                    if is_image:
                        msg_type = MessageType.PHOTO
                        import base64
                        b64_data = base64.b64encode(byte_array).decode("utf-8")
                        data_url = f"data:{doc_mime or 'image/jpeg'};base64,{b64_data}"
                        media_urls.append(data_url)
                        if not text_content:
                            text_content = f"What can you see in this image ({doc_file_name})? Please analyze and describe it in detail."
                    else:
                        msg_type = MessageType.DOCUMENT
                        from aja.config import DATA_DIR
                        uploads_dir = DATA_DIR / "uploads"
                        uploads_dir.mkdir(parents=True, exist_ok=True)
                        safe_name = re.sub(r'[^\w\-_\.]', '_', doc_file_name)
                        save_path = uploads_dir / safe_name
                        save_path.write_bytes(byte_array)

                        # Try to decode as text/code
                        is_text = False
                        decoded_text = ""
                        try:
                            decoded_text = byte_array.decode("utf-8")
                            is_text = True
                        except UnicodeDecodeError:
                            try:
                                decoded_text = byte_array.decode("latin-1")
                                is_text = True
                            except Exception:
                                is_text = False

                        ext = safe_name.split(".")[-1] if "." in safe_name else ""
                        if is_text:
                            if len(decoded_text) <= 50000:
                                doc_block = f"[Attached Document: {safe_name} (saved to `{save_path}`)]\n```{ext}\n{decoded_text}\n```"
                            else:
                                doc_block = (
                                    f"[Attached Document: {safe_name} (saved to `{save_path}`, {len(decoded_text)} chars)]\n"
                                    f"```{ext}\n{decoded_text[:20000]}\n... [truncated: full file at {save_path}]\n```"
                                )
                        else:
                            doc_block = f"[Attached Document: {safe_name} (saved to `{save_path}`, {file_size} bytes)]"

                        if text_content:
                            text_content = f"{doc_block}\n\nUser Instruction: {text_content}"
                        else:
                            text_content = f"{doc_block}\n\nPlease inspect and analyze this attached file: `{safe_name}`."
                except Exception as e:
                    logger.error(f"Failed to download document from Telegram: {e}")

        # Voice Note & Audio Clip
        voice = getattr(update.message, "voice", None)
        audio = getattr(update.message, "audio", None)
        if voice or audio:
            audio_obj = voice or audio
            msg_type = MessageType.AUDIO
            file_size = getattr(audio_obj, "file_size", None) or 0
            duration = getattr(audio_obj, "duration", 0) or 0
            audio_file_name = getattr(audio_obj, "file_name", None) or ("voice.ogg" if voice else "audio.mp3")

            if file_size > TELEGRAM_MAX_FILE_BYTES:
                logger.warning("Skipping Telegram audio download: %s bytes exceeds 20 MiB cap.", file_size)
            else:
                try:
                    tg_file = await context.bot.get_file(audio_obj.file_id)
                    byte_array = await tg_file.download_as_bytearray()
                    from aja.config import DATA_DIR
                    audio_dir = DATA_DIR / "audio"
                    audio_dir.mkdir(parents=True, exist_ok=True)
                    safe_audio_name = f"voice_{update.message.message_id}.ogg" if voice else re.sub(r'[^\w\-_\.]', '_', audio_file_name)
                    save_path = audio_dir / safe_audio_name
                    save_path.write_bytes(byte_array)

                    # Speech-to-Text Transcription via Gemini / Whisper
                    from aja.gateway.audio_transcriber import transcribe_telegram_audio
                    audio_mime = getattr(audio_obj, "mime_type", "") or ("audio/ogg" if voice else "audio/mpeg")
                    transcript = await transcribe_telegram_audio(
                        bytes(byte_array), mime_type=audio_mime, filename=safe_audio_name
                    )

                    if transcript:
                        audio_header = f"🎙️ [Voice Note Transcript ({duration}s)]:\n\"{transcript}\""
                    else:
                        audio_header = (
                            f"🎙️ [Voice note received: {duration}s]\n"
                            f"*(Audio saved locally at `{save_path}`. To enable automatic speech-to-text, "
                            f"set `GOOGLE_API_KEY` or `OPENAI_API_KEY` in your `.env` file.)*"
                        )

                    if text_content:
                        text_content = f"{audio_header}\n\nCaption: {text_content}"
                    else:
                        text_content = audio_header
                except Exception as e:
                    logger.error(f"Failed to process Telegram audio/voice: {e}")

        # Sticker
        sticker = getattr(update.message, "sticker", None)
        if sticker:
            emoji = getattr(sticker, "emoji", "") or "sticker"
            text_content = f"[Sticker: {emoji}]"

        # Location
        location = getattr(update.message, "location", None)
        if location:
            text_content = f"[Location: latitude={location.latitude}, longitude={location.longitude}]"

        # Contact
        contact = getattr(update.message, "contact", None)
        if contact:
            text_content = f"[Contact: {contact.first_name} {getattr(contact, 'last_name', '')} {contact.phone_number}]"

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
        # Ack reaction: cosmetic "seen" signal; silently degrades in groups
        # that disallow reactions or on any API error.
        await _safe_set_reaction(
            self._bot,
            str(update.message.chat_id),
            update.message.message_id,
            ACK_REACTION_EMOJI,
        )
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
        callback_user_id = str(query.from_user.id) if query.from_user else ""

        # ── Local model controls (ls:<idx>, lstp, lref, lstat, luse:<idx>) ──
        if (
            data.startswith(("ls:", "luse:", "local_start:", "local_use:"))
            or data in ("lstp", "lref", "lstat", "local_stop", "local_refresh")
        ):
            self.metrics["callback_handled"] += 1
            from aja.gateway.telegram_local import handle_local_model_callback
            chat_id = str(query.message.chat_id) if (query.message and getattr(query.message, "chat_id", None)) else ""
            authorized, reply_text, reply_markup = await handle_local_model_callback(
                data, callback_user_id, chat_id
            )
            try:
                try:
                    await query.edit_message_text(
                        text=reply_text,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    await query.edit_message_text(
                        text=reply_text,
                        reply_markup=reply_markup,
                    )
            except Exception as e:
                logger.debug("Failed to edit Telegram message on local callback: %s", e)
            return

        # ── Per-command exec approvals (execok_<token> / execno_<token>) ──
        if data.startswith(("execok_", "execno_")):
            self.metrics["callback_handled"] += 1
            allowed_user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID") or getattr(
                __import__("aja.config", fromlist=["TELEGRAM_ALLOWED_USER_ID"]),
                "TELEGRAM_ALLOWED_USER_ID",
                "",
            )
            if (
                not allowed_user_id
                or str(allowed_user_id).strip() in ("", "*")
                or callback_user_id != str(allowed_user_id).strip()
            ):
                logger.critical(
                    "Security Alert: unauthorized exec-approval callback by %s",
                    callback_user_id,
                )
                await query.edit_message_text(text="🚫 Unauthorized callback action.")
                return

            from aja.security.pending_commands import get_pending_command_store
            from aja.security.command_guard import classify_command
            from aja.orchestration.tools.executor import ToolExecutor
            from aja.utils.redact import redact_secrets

            approved = data.startswith("execok_")
            token = data.split("_", 1)[1]
            store = get_pending_command_store()

            pc = store.get(token)
            if pc is None:
                await query.edit_message_text(text="⚠️ Approval request not found or expired.")
                return

            saved_command = pc.command
            handled, message = await store.resolve(
                token, approved, callback_user_id
            )
            if not handled:
                await query.edit_message_text(text=message)
                return

            if not approved:
                await query.edit_message_text(text=f"❌ Execution rejected for: `{redact_secrets(saved_command)}`")
                return

            # Re-classify exact byte string at execution time (TOCTOU guard)
            classification = classify_command(saved_command)
            if classification["decision"] == "deny":
                store.journal_event(
                    "EXEC_DENIED",
                    saved_command,
                    token,
                    f"Denied at execution-time re-classification: {'; '.join(classification.get('reasons', []))}",
                    status="DENIED",
                )
                await query.edit_message_text(
                    text=f"🚫 **Security Alert**: Command `{redact_secrets(saved_command)}` was re-classified as DENIED at execution time and will not run.\nReasons: {'; '.join(classification.get('reasons', []))}"
                )
                return

            await query.edit_message_text(text=f"⏳ Executing approved command: `{redact_secrets(saved_command)}`...")

            executor = ToolExecutor()
            result = await executor.execute_async(saved_command)

            status_str = result.get("status", "unknown")
            exit_code = result.get("code", -1)
            store.journal_event(
                "EXEC_COMPLETED",
                saved_command,
                token,
                f"Command completed: status={status_str}, exit_code={exit_code}",
            )

            status_emoji = "✅" if status_str == "success" and exit_code == 0 else "❌"
            stdout_text = redact_secrets(result.get("stdout", "")).strip()
            stderr_text = redact_secrets(result.get("stderr", "") or result.get("message", "")).strip()

            out_lines = [f"{status_emoji} **Command Completed** (exit code {exit_code}): `{redact_secrets(saved_command)}`"]
            if stdout_text:
                out_lines.append(f"**Stdout**:\n```\n{stdout_text[:2000]}\n```")
            if stderr_text:
                out_lines.append(f"**Stderr**:\n```\n{stderr_text[:1000]}\n```")
            if not stdout_text and not stderr_text:
                out_lines.append("*(No output)*")

            await query.edit_message_text(text="\n\n".join(out_lines))
            return

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

    async def _handle_app(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = getattr(update, "message", None)
        chat_id = getattr(message, "chat_id", None)
        if chat_id is None:
            return

        webapp_url = os.getenv("AJA_WEBAPP_URL") or os.getenv("WEBAPP_URL")
        if webapp_url:
            try:
                from telegram import WebAppInfo

                markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                text="🚀 Launch Mission Control",
                                web_app=WebAppInfo(url=webapp_url),
                            )
                        ]
                    ]
                )
                await self.send_message(
                    str(chat_id),
                    "⚡ *AJA Mission Control*\nTap below to launch live visual control directly inside Telegram:",
                    reply_markup=markup,
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            except Exception as e:
                logger.warning("Failed to build WebApp button: %s", e)

        await self.send_message(
            str(chat_id),
            "⚡ *AJA Mission Control*\n\nLocal Web Dashboard is active at:\n`http://localhost:8000/app`\n\n_To enable Telegram Mini App modal inside this chat, set `AJA_WEBAPP_URL=https://...` (via Cloudflare tunnel, ngrok, or domain SSL)._",
            parse_mode=ParseMode.MARKDOWN,
        )


    async def send_message(self, chat_id: str, text: str, **kwargs) -> Any:
        if not self._bot:
            return None

        reply_to = kwargs.pop("reply_to_message_id", None)
        success_emoji = None
        if reply_to is not None:
            # Final reply to a specific user message: swap the 👀 ack for ✅.
            success_emoji = DONE_REACTION_EMOJI

        # MEDIA: tag extraction — ship referenced files as native documents,
        # strip tags from visible text (Telegram Tier 2 item 6).
        try:
            from aja.gateway.reply_extras import extract_media_tags, send_documents
            text, media_paths = extract_media_tags(text)
        except Exception:
            media_paths = []

        if text is None:
            text = ""
        processed_text = self._prepare_text_for_mobile(str(text))
        parse_mode = kwargs.pop("parse_mode", None)
        reply_markup = kwargs.pop("reply_markup", None)

        # Telegram hard-rejects any message >4096 chars (400 TEXT_TOO_LONG).
        # The old single-shot send silently dropped long final replies, so
        # split on newline/space boundaries and send every part. Decorations
        # (parse_mode / reply_markup) ride only on the final chunk.
        chunks = split_for_telegram(processed_text)
        result = None
        for index, chunk in enumerate(chunks):
            chunk_kwargs = dict(kwargs)
            if index == len(chunks) - 1:
                chunk_kwargs["reply_markup"] = reply_markup
            try:
                result = await self._bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=parse_mode,
                    **chunk_kwargs,
                )
                self.metrics["messages_sent"] += 1
                if index == len(chunks) - 1 and reply_to is not None and success_emoji:
                    # ✅ is not always in Telegram's allowed reaction set;
                    # retry once with 👍, then give up silently.
                    if not await _safe_set_reaction(
                        self._bot, chat_id, reply_to, success_emoji
                    ):
                        await _safe_set_reaction(
                            self._bot, chat_id, reply_to, DONE_REACTION_FALLBACK_EMOJI
                        )
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")
                self.metrics["send_failures"] += 1
                self.metrics["last_error"] = str(e)
                self.metrics["last_error_at"] = datetime.now(timezone.utc).isoformat()

        # Deliver MEDIA: attachments after the text (Tier 2 item 6).
        if media_paths:
            try:
                from aja.gateway.reply_extras import send_documents

                delivered, failed = await send_documents(
                    self._bot, chat_id, media_paths
                )
                self.metrics["documents_sent"] = (
                    self.metrics.get("documents_sent", 0) + len(delivered)
                )
                for fail in failed:
                    logger.warning("Document delivery failed: %s", fail)
            except Exception as e:
                logger.warning("Document delivery crashed: %s", e)
        return result

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
