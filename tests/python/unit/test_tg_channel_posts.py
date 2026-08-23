"""Channel-post / malformed-update hardening tests for TelegramAdapter.

Telegram channel posts and anonymous-admin messages arrive with
``message.from_user is None``; the adapter must skip them cleanly instead of
crashing the polling handler task with AttributeError.
"""

import asyncio
from types import SimpleNamespace

from aja.gateway.tg_client import TelegramAdapter


def _make_adapter() -> TelegramAdapter:
    return TelegramAdapter({"token": "test-token"})


class _FakeMessage:
    """Mirrors the minimal surface of telegram.Message used by _handle_message."""

    def __init__(self, text="hello", message_id=1, from_user=None, chat=None, chat_id=None):
        self.text = text
        self.message_id = message_id
        self.from_user = from_user
        self.chat = chat
        self.chat_id = chat_id


class _FakeUpdate:
    def __init__(self, message):
        self.message = message
        self.callback_query = None


def test_channel_post_without_from_user_is_skipped_cleanly():
    adapter = _make_adapter()
    # Channel-post shape: from_user is None, but chat/chat_id exist.
    msg = _FakeMessage(from_user=None, chat=SimpleNamespace(id=777), chat_id=777)
    update = _FakeUpdate(msg)

    result = asyncio.run(adapter._handle_message(update, None))

    assert result is None
    assert adapter._queue.qsize() == 0
    assert adapter.metrics["events_rejected"] == 1
    assert adapter.metrics["events_received"] == 0


def test_message_without_chat_context_is_skipped():
    adapter = _make_adapter()
    user = SimpleNamespace(id=42)
    msg = _FakeMessage(from_user=user, chat=None, chat_id=None)
    update = _FakeUpdate(msg)

    result = asyncio.run(adapter._handle_message(update, None))

    assert result is None
    assert adapter._queue.qsize() == 0
    assert adapter.metrics["events_rejected"] == 1


def test_missing_message_payload_is_ignored_without_metrics():
    adapter = _make_adapter()
    update = _FakeUpdate(None)

    asyncio.run(adapter._handle_message(update, None))

    assert adapter._queue.qsize() == 0
    assert adapter.metrics["events_received"] == 0
    assert adapter.metrics["events_rejected"] == 0


def test_malformed_photo_array_does_not_crash_handler():
    adapter = _make_adapter()
    user = SimpleNamespace(id=7)
    msg = _FakeMessage(text="", from_user=user, chat=SimpleNamespace(id=5), chat_id=5)
    # Malformed: photo array present but empty (no resolvable sizes).
    msg.photo = []
    update = _FakeUpdate(msg)

    # No text, no usable media -> event dropped without exception.
    result = asyncio.run(adapter._handle_message(update, None))

    assert result is None
    assert adapter._queue.qsize() == 0


def test_callback_update_without_query_payload_is_skipped():
    adapter = _make_adapter()
    update = _FakeUpdate(None)  # callback_query None

    asyncio.run(adapter._handle_callback(update, None))

    assert adapter.metrics["events_rejected"] == 1
