"""Telegram surface adapter speaking the universal Envelope protocol.

Thin translator only: converts between python-telegram-bot native objects and
aja.messaging.envelope types. All business logic lives in ConversationCore.
Intended to replace tg_client.py after the migration window.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from aja.gateway.base import BasePlatformAdapter
from aja.gateway.render import MobileMDRenderer
from aja.messaging.envelope import Attachment, Envelope, InboundMessage, Kind, Widget

logger = logging.getLogger(__name__)

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        filters,
        MessageHandler as PTBMessageHandler,
    )

    TELEGRAM_AVAILABLE = True
except Exception:  # pragma: no cover - soft dependency
    TELEGRAM_AVAILABLE = False

STREAM_MIN_INTERVAL_SECONDS = 1.0
STREAM_STATE_TTL_SECONDS = 1800.0
STREAM_MAX_CONSECUTIVE_ERRORS = 3
TELEGRAM_TEXT_LIMIT = 4096
_TRUNCATION_MARKER = " …[truncated]"


def _cap_stream_text(text: str) -> str:
    """Clamp streamed text to Telegram's 4096-char message limit."""
    if len(text) <= TELEGRAM_TEXT_LIMIT:
        return text
    return text[: TELEGRAM_TEXT_LIMIT - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


@dataclass
class Capabilities:
    """What this surface supports, so ConversationCore can adapt output."""

    streaming_edit: bool = True
    buttons: bool = True
    images_in: bool = True
    images_out: bool = False
    voice_in: bool = False
    markdown_parse_mode: str = "MarkdownV2"


def _action_id_from_callback_data(data: str) -> str:
    if data.startswith(("approve", "reject")):
        return f"perm:{data}"
    return data


def _callback_data_from_action_id(action_id: str) -> str:
    parts = action_id.split(":")
    if parts and parts[0] == "perm":
        return ":".join(parts[1:])
    return action_id


class TelegramEnvelopeAdapter(BasePlatformAdapter):
    """Telegram surface adapter speaking universal Envelope protocol."""

    def __init__(self, config: Dict[str, Any] | str):
        if isinstance(config, str):
            config = {"token": config}
        super().__init__(config)
        self.token = config.get("token")
        self.name = "telegram"
        self._app: Optional[Application] = None
        self._bot: Any = None
        self._on_envelope: Optional[Callable[[InboundMessage], Awaitable]] = None
        self._inbound: asyncio.Queue = asyncio.Queue()
        self._stream_states: Dict[str, Dict[str, Any]] = {}
        self._clock: Callable[[], float] = time.monotonic
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
            "queue_size": 0,
        }

    def capabilities(self) -> Capabilities:
        return Capabilities()

    async def start(
        self, on_envelope: Optional[Callable[[InboundMessage], Awaitable]] = None
    ) -> None:
        self._on_envelope = on_envelope
        if not TELEGRAM_AVAILABLE or not self.token:
            logger.error("Telegram adapter cannot start (missing lib or token).")
            return
        self._app = Application.builder().token(self.token).build()
        self._bot = self._app.bot
        self._app.add_handler(
            PTBMessageHandler(filters.TEXT | filters.PHOTO, self._handle_update)
        )
        self._app.add_handler(CallbackQueryHandler(self._handle_update))
        for attempt in range(5):
            try:
                await self._app.initialize()
                await self._app.start()
                await self._app.updater.start_polling(drop_pending_updates=True)
                self.is_running = True
                return
            except Exception as e:
                wait = min(2**attempt, 30)
                self.metrics["poll_retries"] += 1
                self.metrics["last_error"] = str(e)
                self.metrics["last_error_at"] = self._utc_now_iso()
                logger.error("Telegram connect attempt %s failed: %s", attempt + 1, e)
                await asyncio.sleep(wait)

    async def stop(self) -> None:
        self.is_running = False
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                logger.debug("Telegram adapter shutdown error: %s", e)
        self._stream_states.clear()

    async def listen(self) -> AsyncIterator[InboundMessage]:
        while True:
            msg = await self._inbound.get()
            self.metrics["events_dequeued"] += 1
            self.metrics["queue_size"] = self._inbound.qsize()
            yield msg

    def render(self, env: Envelope) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"text": env.text or ""}
        parse_mode = env.meta.get("parse_mode")
        if parse_mode is None and env.meta.get("markdown"):
            parse_mode = self.capabilities().markdown_parse_mode
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        button_widgets = [w for w in env.widgets if w.type == "button"]
        if button_widgets and TELEGRAM_AVAILABLE:
            rows: List[List[Any]] = []
            current_row: List[Any] = []
            for widget in button_widgets:
                current_row.append(self._render_button(widget))
                if widget.payload.get("row_end") or len(current_row) >= 2:
                    rows.append(current_row)
                    current_row = []
            if current_row:
                rows.append(current_row)
            kwargs["reply_markup"] = InlineKeyboardMarkup(rows)
        elif env.widgets:
            kwargs["reply_markup"] = {"buttons": [_widget_dict(w) for w in env.widgets]}
        return kwargs

    def _render_button(self, widget: Widget) -> Any:
        callback_data = _callback_data_from_action_id(widget.action_id)[:64]
        return InlineKeyboardButton(widget.label, callback_data=callback_data)

    async def send_message(self, chat_id: str, text: str, **kwargs: Any) -> Any:
        if not self._bot:
            return None
        try:
            result = await self._bot.send_message(
                chat_id=chat_id,
                text=MobileMDRenderer.render(str(text or "")),
                **kwargs,
            )
            self.metrics["messages_sent"] += 1
            return result
        except Exception as e:
            self._record_send_failure(e)
            return None

    async def send_notification(
        self, chat_id: str, text: str, importance: str = "normal"
    ) -> Any:
        return await self.send_message(
            chat_id, text, disable_notification=(importance == "low")
        )

    async def send_envelope(self, env: Envelope) -> Any:
        if env.kind == Kind.STREAM_CHUNK:
            return await self._handle_stream_chunk(env)
        if env.kind == Kind.IMAGE and env.attachments:
            return await self._send_image(env)
        rendered = self.render(env)
        reply_markup = rendered.pop("reply_markup", None)
        return await self.send_message(
            env.chat_id, rendered.get("text", ""), reply_markup=reply_markup,
            **{k: v for k, v in rendered.items() if k != "text"},
        )

    async def _send_image(self, env: Envelope) -> Any:
        if not self._bot:
            return None
        attachment = env.attachments[0]
        try:
            photo = attachment.data
            if photo is None and attachment.url:
                photo = attachment.url
            caption = env.meta.get("caption", "")
            result = await self._bot.send_photo(chat_id=env.chat_id, photo=photo, caption=caption)
            self.metrics["messages_sent"] += 1
            return result
        except Exception as e:
            self._record_send_failure(e)
            return None

    async def _handle_stream_chunk(self, env: Envelope) -> Any:
        key = env.correlation_id or env.chat_id
        self._prune_stale_stream_states()
        state = self._stream_states.get(key)
        if state is None:
            state = {
                "buffer": "",
                "message_id": None,
                "last_edit": float("-inf"),
                "consecutive_edit_errors": 0,
                "created_at": self._clock(),
            }
            self._stream_states[key] = state
        state["buffer"] = (state["buffer"] or "") + (env.text or "")
        now = self._clock()
        is_edit = state["message_id"] is not None
        if is_edit and now - state["last_edit"] < STREAM_MIN_INTERVAL_SECONDS:
            return None
        if not self._bot:
            return None
        text = _cap_stream_text(state["buffer"])

        async def _call() -> Any:
            if is_edit:
                return await self._bot.edit_message_text(
                    text=text, chat_id=env.chat_id, message_id=state["message_id"]
                )
            result = await self._bot.send_message(chat_id=env.chat_id, text=text)
            state["message_id"] = getattr(result, "message_id", None)
            return result

        def _succeed(result: Any) -> Any:
            state["last_edit"] = now
            state["consecutive_edit_errors"] = 0
            self.metrics["messages_sent"] += 1
            return result

        try:
            return _succeed(await _call())
        except Exception as exc:
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is not None:
                logger.warning(
                    "Telegram stream %s rate-limited (429); sleeping %ss once.",
                    "edit" if is_edit else "send",
                    retry_after,
                )
                await asyncio.sleep(float(retry_after))
                try:
                    return _succeed(await _call())
                except Exception as retry_exc:
                    exc = retry_exc
            lowered = str(exc).lower()
            if "message is not modified" in lowered or "message to edit not found" in lowered:
                # Terminal: the preview message is gone/identical; drop the
                # state so the next chunk starts a fresh message.
                logger.info("Finalizing Telegram stream state %r (%s).", key, lowered.strip())
                self._stream_states.pop(key, None)
                return None
            state["consecutive_edit_errors"] = state.get("consecutive_edit_errors", 0) + 1
            self.metrics["send_failures"] += 1
            self.metrics["last_error"] = str(exc)
            self.metrics["last_error_at"] = self._utc_now_iso()
            logger.error(
                "Telegram stream %s failed (%d consecutive): %s",
                "edit" if is_edit else "send",
                state["consecutive_edit_errors"],
                exc,
            )
            if state["consecutive_edit_errors"] >= STREAM_MAX_CONSECUTIVE_ERRORS:
                logger.warning(
                    "Telegram stream circuit breaker tripped for %r; sending a fresh final message.",
                    key,
                )
                try:
                    fresh = await self._bot.send_message(
                        chat_id=env.chat_id, text=_cap_stream_text(state["buffer"])
                    )
                    self.metrics["messages_sent"] += 1
                    self._stream_states.pop(key, None)
                    return fresh
                except Exception as fresh_err:
                    logger.error("Telegram fresh-message fallback failed: %s", fresh_err)
                    self._stream_states.pop(key, None)
                return None
            if is_edit:
                # Consume the throttle window even on failure so retries are
                # naturally spaced instead of hammering the API.
                state["last_edit"] = now
            return None

    def _prune_stale_stream_states(self) -> None:
        now = self._clock()
        stale = [
            key
            for key, state in self._stream_states.items()
            if now - float(state.get("created_at", now)) > STREAM_STATE_TTL_SECONDS
        ]
        for key in stale:
            logger.debug("Pruning aged Telegram stream state %r.", key)
            self._stream_states.pop(key, None)

    async def _handle_update(self, update: Any, context: Any = None) -> None:
        query = getattr(update, "callback_query", None)
        if query is not None:
            await self._handle_callback(query)
            return
        message = getattr(update, "message", None)
        if message is not None:
            await self._handle_message(update, message)

    async def _handle_message(self, update: Any, message: Any) -> None:
        from_user = getattr(message, "from_user", None)
        if from_user is None:
            logger.info("Skipping Telegram message without from_user.")
            self.metrics["events_rejected"] += 1
            return
        user_id = str(from_user.id)
        if not self._authorize(user_id):
            self.metrics["events_rejected"] += 1
            return
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        attachments: List[Attachment] = []
        kind = Kind.TEXT
        photos = getattr(message, "photo", None)
        if photos:
            kind = Kind.IMAGE
            attachment = await self._download_photo(photos[-1])
            if attachment is not None:
                attachments.append(attachment)
            if not text:
                text = "What can you see in this image? Please analyze and describe it."
        if not text and not attachments:
            self.metrics["events_rejected"] += 1
            return
        inbound = InboundMessage(
            surface="telegram",
            chat_id=str(getattr(message, "chat_id", "")),
            user_id=user_id,
            text=text,
            attachments=attachments,
            kind=kind,
            raw=update,
        )
        self.metrics["events_received"] += 1
        self.metrics["queue_size"] = self._inbound.qsize() + 1
        await self._inbound.put(inbound)
        if self._on_envelope is not None:
            await self._on_envelope(inbound)

    async def _download_photo(self, photo: Any) -> Optional[Attachment]:
        try:
            tg_file = await self._bot.get_file(photo.file_id)
            data = await tg_file.download_as_bytearray()
            return Attachment(
                kind="image",
                url=f"file://{photo.file_id}",
                data=base64.b64encode(bytes(data)).decode("utf-8"),
                mime="image/jpeg",
                name=str(photo.file_id),
            )
        except Exception as e:
            logger.error("Failed to download Telegram photo: %s", e)
            self.metrics["last_error"] = str(e)
            self.metrics["last_error_at"] = self._utc_now_iso()
            return None

    async def _handle_callback(self, query: Any) -> None:
        answer = getattr(query, "answer", None)
        if answer is not None:
            await answer()
        data = getattr(query, "data", "") or ""
        user = getattr(query, "from_user", None)
        user_id = str(user.id) if user else ""
        if not self._authorize(user_id):
            logger.warning("Unauthorized Telegram callback by user %s", user_id)
            edit = getattr(query, "edit_message_text", None)
            if edit is not None:
                try:
                    await edit(text="🚫 Unauthorized callback action.")
                except Exception as e:
                    logger.debug("Could not flag unauthorized callback: %s", e)
            self.metrics["events_rejected"] += 1
            return
        self.metrics["callback_handled"] += 1
        inbound = InboundMessage(
            surface="telegram",
            chat_id=self._query_chat_id(query),
            user_id=user_id,
            text=_action_id_from_callback_data(data),
            kind=Kind.CALLBACK,
            raw=query,
        )
        self.metrics["events_received"] += 1
        await self._inbound.put(inbound)
        if self._on_envelope is not None:
            await self._on_envelope(inbound)

    @staticmethod
    def _query_chat_id(query: Any) -> str:
        message = getattr(query, "message", None) or getattr(query, "effective_message", None)
        chat = getattr(message, "chat", None)
        return str(getattr(chat, "id", "")) if chat else ""

    def _authorize(self, user_id: str) -> bool:
        from aja.gateway.auth import is_user_authorized

        return is_user_authorized("telegram", user_id)

    def get_health_snapshot(self) -> Dict[str, Any]:
        return {"adapter": self.name, "is_running": self.is_running, **self.metrics}

    def _record_send_failure(self, error: Exception) -> None:
        logger.error("Telegram send failed: %s", error)
        self.metrics["send_failures"] += 1
        self.metrics["last_error"] = str(error)
        self.metrics["last_error_at"] = self._utc_now_iso()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


def _widget_dict(widget: Widget) -> Dict[str, Any]:
    return {"type": widget.type, "label": widget.label, "action_id": widget.action_id}
