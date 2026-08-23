"""Tests for the Envelope-protocol Telegram adapter (telegram_envelope.py)."""
import asyncio
import base64

import pytest

from aja.gateway.adapters.telegram_envelope import (
    Capabilities,
    TelegramEnvelopeAdapter,
    _callback_data_from_action_id,
)
from aja.messaging.envelope import Attachment, Envelope, InboundMessage, Kind, Widget


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


class FakePhotoSize:
    def __init__(self, file_id):
        self.file_id = file_id


class FakeFile:
    def __init__(self, payload: bytes):
        self._payload = payload

    async def download_as_bytearray(self):
        return bytearray(self._payload)


class FakeFromUser:
    def __init__(self, uid):
        self.id = uid


class FakeMessage:
    def __init__(self, chat_id=100, user_id=42, text="hi", photos=None):
        self.chat_id = chat_id
        self.chat = type("Chat", (), {"id": chat_id})()
        self.from_user = FakeFromUser(user_id) if user_id else None
        self.text = text
        self.caption = None
        self.photo = photos
        self.message_id = 555


class FakeCallbackQuery:
    def __init__(self, data, user_id=42, chat_id=100):
        self.data = data
        self.from_user = FakeFromUser(user_id)
        self.message = type("Msg", (), {"chat": type("Chat", (), {"id": chat_id})()})()
        self.answered = False
        self.edits = []

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text=None, **kwargs):
        self.edits.append(text)


class FakeUpdate:
    def __init__(self, message=None, callback_query=None):
        self.message = message
        self.callback_query = callback_query


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.photos = []
        self.files = {}
        self.next_message_id = 900

    async def send_message(self, chat_id, text, **kwargs):
        result = type("Msg", (), {"message_id": self.next_message_id})()
        self.next_message_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return result

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, **kwargs):
        self.edits.append({"text": text, "chat_id": chat_id, "message_id": message_id})
        return True

    async def send_photo(self, chat_id, photo, caption="", **kwargs):
        self.photos.append({"chat_id": chat_id, "photo": photo, "caption": caption})
        return True

    async def get_file(self, file_id):
        return self.files.get(file_id)


def make_adapter(monkeypatch, allowed="42"):
    if allowed is None:
        monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", allowed)
    adapter = TelegramEnvelopeAdapter({"token": "test-token"})
    adapter._bot = FakeBot()
    return adapter


@pytest.mark.anyio
async def test_inbound_text_becomes_inbound_message(monkeypatch):
    adapter = make_adapter(monkeypatch)
    update = FakeUpdate(message=FakeMessage(text="hello aja"))
    await adapter._handle_update(update)
    msg = adapter._inbound.get_nowait()
    assert isinstance(msg, InboundMessage)
    assert msg.surface == "telegram"
    assert msg.chat_id == "100"
    assert msg.user_id == "42"
    assert msg.text == "hello aja"
    assert msg.kind == Kind.TEXT
    assert adapter.metrics["events_received"] == 1


@pytest.mark.anyio
async def test_callback_fires_on_envelope_hook(monkeypatch):
    adapter = make_adapter(monkeypatch)
    seen = []

    async def on_envelope(msg):
        seen.append(msg)

    adapter._on_envelope = on_envelope
    await adapter._handle_update(FakeUpdate(message=FakeMessage(text="ping")))
    assert len(seen) == 1
    assert seen[0].text == "ping"


@pytest.mark.anyio
async def test_photo_becomes_image_envelope_with_base64(monkeypatch):
    adapter = make_adapter(monkeypatch)
    payload = b"\x89PNG-fake-image-bytes"
    adapter._bot.files["photo-abc"] = FakeFile(payload)
    msg = FakeMessage(text="", photos=[FakePhotoSize("small"), FakePhotoSize("photo-abc")])
    await adapter._handle_update(FakeUpdate(message=msg))
    inbound = adapter._inbound.get_nowait()
    assert inbound.kind == Kind.IMAGE
    assert len(inbound.attachments) == 1
    att = inbound.attachments[0]
    assert att.kind == "image"
    decoded = base64.b64decode(att.data.encode("utf-8"))
    assert decoded == payload
    assert att.mime == "image/jpeg"
    assert inbound.text  # fallback prompt filled in for empty caption


@pytest.mark.anyio
async def test_callback_becomes_callback_envelope(monkeypatch):
    adapter = make_adapter(monkeypatch)
    query = FakeCallbackQuery(data="approve:MISSION-1")
    await adapter._handle_update(FakeUpdate(callback_query=query))
    assert query.answered
    inbound = adapter._inbound.get_nowait()
    assert inbound.kind == Kind.CALLBACK
    assert inbound.text == "perm:approve:MISSION-1"
    assert adapter.metrics["callback_handled"] == 1


def test_action_id_roundtrip():
    assert _callback_data_from_action_id("perm:approve:X") == "approve:X"
    assert _callback_data_from_action_id("reminder:snooze:J1") == "reminder:snooze:J1"


def test_render_widgets_to_inline_keyboard(monkeypatch):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    adapter = make_adapter(monkeypatch)
    env = Envelope(
        surface="telegram",
        chat_id="100",
        text="Approve this?",
        widgets=[
            Widget(type="button", label="✅ Approve", action_id="perm:approve:X"),
            Widget(type="button", label="❌ Reject", action_id="perm:reject:X"),
        ],
    )
    rendered = adapter.render(env)
    markup = rendered["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert all(isinstance(b, InlineKeyboardButton) for b in buttons)
    assert [(b.text, b.callback_data) for b in buttons] == [
        ("✅ Approve", "approve:X"),
        ("❌ Reject", "reject:X"),
    ]


@pytest.mark.anyio
async def test_unauthorized_user_skipped(monkeypatch):
    adapter = make_adapter(monkeypatch, allowed="42")
    update = FakeUpdate(message=FakeMessage(user_id=99, text="intrude"))
    await adapter._handle_update(update)
    assert adapter._inbound.qsize() == 0
    assert adapter.metrics["events_rejected"] == 1

    bad_query = FakeCallbackQuery(data="approve:M1", user_id=99)
    await adapter._handle_update(FakeUpdate(callback_query=bad_query))
    assert bad_query.answered
    assert bad_query.edits == ["🚫 Unauthorized callback action."]
    assert adapter._inbound.qsize() == 0


def test_capabilities_defaults():
    caps = Capabilities()
    assert caps.streaming_edit and caps.buttons and caps.images_in
    assert not caps.images_out and not caps.voice_in
    assert caps.markdown_parse_mode == "MarkdownV2"


@pytest.mark.anyio
async def test_streaming_edit_throttled_to_one_per_second(monkeypatch):
    adapter = make_adapter(monkeypatch)
    clock = {"t": 0.0}
    adapter._clock = lambda: clock["t"]

    base = Envelope(surface="telegram", chat_id="100", correlation_id="conv1")
    chunk1 = base.stream_chunk("Hello")
    await adapter.send_envelope(chunk1)
    chunk2 = base.stream_chunk(" world")
    await adapter.send_envelope(chunk2)

    bot = adapter._bot
    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == "Hello"
    assert bot.edits == []

    clock["t"] += 1.5
    await adapter.send_envelope(base.stream_chunk(" more"))
    assert len(bot.edits) == 1
    assert bot.edits[0]["text"] == "Hello world more"
    assert bot.edits[0]["message_id"] == 900

