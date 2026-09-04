"""Unit tests for Telegram Interactivity, Continuous Typing, Reactions, and Multimodal Message Ingestion."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import aja.gateway.tg_client as tg_client
from aja.gateway.base import MessageType


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.deletes = []
        self.reactions = []
        self.chat_actions = []

    async def send_message(self, chat_id, text, **kwargs):
        msg = SimpleNamespace(chat_id=chat_id, text=text, message_id=101, **kwargs)
        self.sent.append(msg)
        return msg

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edits.append((chat_id, message_id, text, kwargs))

    async def delete_message(self, chat_id, message_id):
        self.deletes.append((chat_id, message_id))

    async def set_message_reaction(self, chat_id, message_id, reaction, **kwargs):
        self.reactions.append((chat_id, message_id, reaction))

    async def send_chat_action(self, chat_id, action, **kwargs):
        self.chat_actions.append((chat_id, action))


class FakeTgFile:
    def __init__(self, data: bytes):
        self.data = data

    async def download_as_bytearray(self):
        return bytearray(self.data)


# ------------------------------------------------------------- Continuous Typing


@pytest.mark.anyio
async def test_continuous_chat_action_pulses_and_cancels():
    bot = FakeBot()
    async with tg_client.continuous_chat_action(bot, "100", action="typing", interval=0.03):
        await asyncio.sleep(0.08)

    # Should have pulsed multiple times
    assert len(bot.chat_actions) >= 2
    for chat_id, action in bot.chat_actions:
        assert chat_id == "100"
        assert action == "typing"


@pytest.mark.anyio
async def test_continuous_chat_action_safe_degradation():
    class BrokenBot(FakeBot):
        async def send_chat_action(self, chat_id, action, **kwargs):
            raise RuntimeError("Telegram API timeout")

    # Exploding bot never crashes caller
    async with tg_client.continuous_chat_action(BrokenBot(), "100", action="typing", interval=0.02):
        await asyncio.sleep(0.05)

    # None bot / empty chat_id degrades silently
    async with tg_client.continuous_chat_action(None, "100"):
        pass
    async with tg_client.continuous_chat_action(FakeBot(), ""):
        pass


# ------------------------------------------------------------- Document Ingestion


@pytest.mark.anyio
async def test_handle_document_code_file(tmp_path, monkeypatch):
    monkeypatch.setattr("aja.config.DATA_DIR", tmp_path)
    bot = FakeBot()
    adapter = tg_client.TelegramAdapter("fake:token")
    adapter._bot = bot

    code_bytes = b"def greet():\n    return 'hello world'\n"
    fake_tg_file = FakeTgFile(code_bytes)

    context = SimpleNamespace(
        bot=SimpleNamespace(get_file=AsyncMock(return_value=fake_tg_file))
    )

    fake_doc = SimpleNamespace(
        file_id="doc123",
        file_name="script.py",
        mime_type="text/x-python",
        file_size=len(code_bytes),
    )

    update = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=123,
            from_user=SimpleNamespace(id=456),
            chat=SimpleNamespace(id=123),
            message_id=999,
            text="",
            caption="Please refactor this code",
            photo=None,
            document=fake_doc,
            voice=None,
            audio=None,
            sticker=None,
            location=None,
            contact=None,
        )
    )

    event = await adapter._handle_message(update, context)
    assert event is not None
    assert event.message_type == MessageType.DOCUMENT
    assert "[Attached Document: script.py" in event.text
    assert "def greet():" in event.text
    assert "Please refactor this code" in event.text
    # Ack reaction was placed
    assert len(bot.reactions) == 1
    assert bot.reactions[0][0] == "123"
    assert bot.reactions[0][1] == 999


@pytest.mark.anyio
async def test_handle_document_uncompressed_image():
    bot = FakeBot()
    adapter = tg_client.TelegramAdapter("fake:token")
    adapter._bot = bot

    image_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    fake_tg_file = FakeTgFile(image_bytes)

    context = SimpleNamespace(
        bot=SimpleNamespace(get_file=AsyncMock(return_value=fake_tg_file))
    )

    fake_doc = SimpleNamespace(
        file_id="img_doc123",
        file_name="mockup.png",
        mime_type="image/png",
        file_size=len(image_bytes),
    )

    update = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=123,
            from_user=SimpleNamespace(id=456),
            chat=SimpleNamespace(id=123),
            message_id=999,
            text="",
            caption=None,
            photo=None,
            document=fake_doc,
            voice=None,
            audio=None,
            sticker=None,
            location=None,
            contact=None,
        )
    )

    event = await adapter._handle_message(update, context)
    assert event is not None
    assert event.message_type == MessageType.PHOTO
    assert len(event.media_urls) == 1
    assert event.media_urls[0].startswith("data:image/png;base64,")


@pytest.mark.anyio
async def test_handle_document_oversized():
    bot = FakeBot()
    adapter = tg_client.TelegramAdapter("fake:token")
    adapter._bot = bot

    fake_doc = SimpleNamespace(
        file_id="huge123",
        file_name="dataset.iso",
        mime_type="application/octet-stream",
        file_size=30 * 1024 * 1024,  # 30 MB > 20 MB cap
    )

    update = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=123,
            from_user=SimpleNamespace(id=456),
            chat=SimpleNamespace(id=123),
            message_id=999,
            text="",
            caption=None,
            photo=None,
            document=fake_doc,
            voice=None,
            audio=None,
            sticker=None,
            location=None,
            contact=None,
        )
    )

    context = SimpleNamespace(bot=bot)
    event = await adapter._handle_message(update, context)
    assert event is None
    assert len(bot.sent) == 1
    assert "> 20 MB limit" in bot.sent[0].text


# ------------------------------------------------------------- Voice & Audio Ingestion


@pytest.mark.anyio
async def test_handle_voice_message_with_transcription(tmp_path, monkeypatch):
    monkeypatch.setattr("aja.config.DATA_DIR", tmp_path)
    bot = FakeBot()
    adapter = tg_client.TelegramAdapter("fake:token")
    adapter._bot = bot

    voice_bytes = b"OggS\x00\x02\x00\x00"
    fake_tg_file = FakeTgFile(voice_bytes)

    context = SimpleNamespace(
        bot=SimpleNamespace(get_file=AsyncMock(return_value=fake_tg_file))
    )

    fake_voice = SimpleNamespace(
        file_id="voice123",
        duration=5,
        file_size=len(voice_bytes),
        mime_type="audio/ogg",
    )

    update = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=123,
            from_user=SimpleNamespace(id=456),
            chat=SimpleNamespace(id=123),
            message_id=777,
            text="",
            caption="",
            photo=None,
            document=None,
            voice=fake_voice,
            audio=None,
            sticker=None,
            location=None,
            contact=None,
        )
    )

    with patch(
        "aja.gateway.audio_transcriber.transcribe_telegram_audio",
        new=AsyncMock(return_value="what models do we have"),
    ):
        event = await adapter._handle_message(update, context)
        assert event is not None
        assert event.message_type == MessageType.AUDIO
        assert "🎙️ [Voice Note Transcript (5s)]:" in event.text
        assert "what models do we have" in event.text


@pytest.mark.anyio
async def test_handle_voice_message_fallback_without_keys(tmp_path, monkeypatch):
    monkeypatch.setattr("aja.config.DATA_DIR", tmp_path)
    bot = FakeBot()
    adapter = tg_client.TelegramAdapter("fake:token")
    adapter._bot = bot

    voice_bytes = b"OggS\x00\x02\x00\x00"
    fake_tg_file = FakeTgFile(voice_bytes)

    context = SimpleNamespace(
        bot=SimpleNamespace(get_file=AsyncMock(return_value=fake_tg_file))
    )

    fake_voice = SimpleNamespace(
        file_id="voice123",
        duration=3,
        file_size=len(voice_bytes),
        mime_type="audio/ogg",
    )

    update = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=123,
            from_user=SimpleNamespace(id=456),
            chat=SimpleNamespace(id=123),
            message_id=777,
            text="",
            caption="",
            photo=None,
            document=None,
            voice=fake_voice,
            audio=None,
            sticker=None,
            location=None,
            contact=None,
        )
    )

    with patch(
        "aja.gateway.audio_transcriber.transcribe_telegram_audio",
        new=AsyncMock(return_value=None),
    ):
        event = await adapter._handle_message(update, context)
        assert event is not None
        assert event.message_type == MessageType.AUDIO
        assert "Audio saved locally" in event.text
        assert "GOOGLE_API_KEY" in event.text


# ------------------------------------------------------------- Sticker & Location


@pytest.mark.anyio
async def test_handle_sticker_and_location():
    bot = FakeBot()
    adapter = tg_client.TelegramAdapter("fake:token")
    adapter._bot = bot

    # Sticker update
    update_sticker = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=123,
            from_user=SimpleNamespace(id=456),
            chat=SimpleNamespace(id=123),
            message_id=101,
            text="",
            caption="",
            photo=None,
            document=None,
            voice=None,
            audio=None,
            sticker=SimpleNamespace(emoji="🚀"),
            location=None,
            contact=None,
        )
    )
    event_sticker = await adapter._handle_message(update_sticker, SimpleNamespace(bot=bot))
    assert event_sticker is not None
    assert event_sticker.text == "[Sticker: 🚀]"

    # Location update
    update_loc = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=123,
            from_user=SimpleNamespace(id=456),
            chat=SimpleNamespace(id=123),
            message_id=102,
            text="",
            caption="",
            photo=None,
            document=None,
            voice=None,
            audio=None,
            sticker=None,
            location=SimpleNamespace(latitude=37.7749, longitude=-122.4194),
            contact=None,
        )
    )
    event_loc = await adapter._handle_message(update_loc, SimpleNamespace(bot=bot))
    assert event_loc is not None
    assert "latitude=37.7749" in event_loc.text
    assert "longitude=-122.4194" in event_loc.text
