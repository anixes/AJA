import json
from types import SimpleNamespace

from aja.gateway.base import MessageEvent, MessageType
from aja.gateway.orchestrator import UnifiedGateway
from aja.gateway.tg_client import TelegramAdapter


class _FakeQuery:
    def __init__(self, data: str, user_id: int):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.edited_text = None

    async def answer(self):
        return None

    async def edit_message_text(self, text: str):
        self.edited_text = text
        return None


class _FakeUpdate:
    def __init__(self, query: _FakeQuery):
        self.callback_query = query


def test_orchestrator_authorization_uses_user_id(monkeypatch):
    import aja.gateway.orchestrator as orchestrator_mod

    monkeypatch.setattr(orchestrator_mod, "TELEGRAM_ALLOWED_USER_ID", "42")
    gateway = object.__new__(UnifiedGateway)
    event = MessageEvent(
        platform="telegram",
        chat_id="999",
        user_id="42",
        message_type=MessageType.TEXT,
        text="status",
    )

    assert gateway._is_telegram_user_authorized(event) is True


def test_low_priority_throttle_blocks_fast_repeats(monkeypatch):
    monkeypatch.setenv("AJA_TELEGRAM_LOW_PRIORITY_MIN_INTERVAL_SECONDS", "60")
    adapter = TelegramAdapter({"token": "test-token"})

    assert adapter._should_emit_low_priority("chat-1", "tick") is True
    assert adapter._should_emit_low_priority("chat-1", "tick") is False


def test_callback_rejects_unauthorized_user(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123")
    adapter = TelegramAdapter({"token": "test-token"})
    query = _FakeQuery("approve_M-001", user_id=999)

    import asyncio
    asyncio.run(adapter._handle_callback(_FakeUpdate(query), None))

    assert "Unauthorized" in (query.edited_text or "")


def test_callback_reports_already_handled_status(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123")
    adapter = TelegramAdapter({"token": "test-token"})
    query = _FakeQuery("approve_M-001", user_id=123)

    class _FakeMemory:
        def get_mission(self, mission_id):
            return {"mission_id": mission_id, "status": "ACTIVE", "metadata_json": "{}"}

    import aja.memory.secretary as secretary_mod

    monkeypatch.setattr(secretary_mod, "AJAMemory", lambda: _FakeMemory())

    import asyncio
    asyncio.run(adapter._handle_callback(_FakeUpdate(query), None))

    assert "already handled" in (query.edited_text or "").lower()


def test_callback_reports_expired_approval(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123")
    adapter = TelegramAdapter({"token": "test-token"})
    query = _FakeQuery("approve_M-002", user_id=123)

    class _FakeMemory:
        def get_mission(self, mission_id):
            return {
                "mission_id": mission_id,
                "status": "AWAITING_APPROVAL",
                "metadata_json": json.dumps({"expires_at": "2000-01-01T00:00:00+00:00"}),
            }

    import aja.memory.secretary as secretary_mod

    monkeypatch.setattr(secretary_mod, "AJAMemory", lambda: _FakeMemory())

    import asyncio
    asyncio.run(adapter._handle_callback(_FakeUpdate(query), None))

    assert "expired" in (query.edited_text or "").lower()


def test_send_message_handles_reply_markup_once():
    adapter = TelegramAdapter({"token": "test-token"})
    sent = {}

    class _FakeBot:
        async def send_message(self, **kwargs):
            sent.update(kwargs)
            return {"ok": True}

    adapter._bot = _FakeBot()

    import asyncio
    result = asyncio.run(adapter.send_message("chat-1", "hello", reply_markup="rm"))

    assert result == {"ok": True}
    assert sent.get("reply_markup") == "rm"
    assert adapter.metrics["messages_sent"] == 1


def test_command_and_text_message_routing_to_queue():
    from telegram import User, Chat
    from telegram.constants import ChatType

    adapter = TelegramAdapter({"token": "test-token"})

    # Mock python-telegram-bot structures
    user = User(id=123, first_name="TestUser", is_bot=False)
    chat = Chat(id=456, type=ChatType.PRIVATE)
    
    # Mock the Update object
    class FakeMessage:
        def __init__(self, text, message_id):
            self.text = text
            self.chat_id = 456
            self.from_user = user
            self.message_id = message_id

    class FakeUpdate:
        def __init__(self, text, message_id):
            self.message = FakeMessage(text, message_id)
            self.callback_query = None

    import asyncio

    # Send a plain text message
    update_text = FakeUpdate("Hello there", 1)
    asyncio.run(adapter._handle_text_message(update_text, None))

    # Send a command message (starts with slash)
    update_cmd = FakeUpdate("/run do something", 2)
    asyncio.run(adapter._handle_text_message(update_cmd, None))

    # Dequeue and verify
    assert adapter._queue.qsize() == 2
    
    # Verify the plain text message
    event1 = asyncio.run(adapter._queue.get())
    assert event1.text == "Hello there"
    assert event1.user_id == "123"
    assert event1.chat_id == "456"

    # Verify the command message
    event2 = asyncio.run(adapter._queue.get())
    assert event2.text == "/run do something"
    assert event2.user_id == "123"
    assert event2.chat_id == "456"

