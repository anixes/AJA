"""Tests for DiscordEnvelopeAdapter fixes: bot-task lifecycle, button auth/approvals, listen() sentinel."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from aja.gateway.adapters import discord_envelope as de


@pytest.fixture
def fake_discord(monkeypatch):
    """Injects a fake discord.py so start() runs without network or the real lib."""
    class FakeIntents:
        @staticmethod
        def default():
            return SimpleNamespace(message_content=False)

    class FakeBot:
        instances = []

        def __init__(self, command_prefix=None, intents=None):
            self.command_prefix = command_prefix
            self.intents = intents
            self.closed = False
            type(self).instances.append(self)

        def event(self, coro):
            return coro

        async def start(self, token):
            self.token = token
            await asyncio.sleep(3600)

        async def close(self):
            self.closed = True

    fake_commands = SimpleNamespace(Bot=FakeBot)
    fake_ext = SimpleNamespace(commands=fake_commands)
    fake = SimpleNamespace(
        Intents=FakeIntents,
        commands=fake_commands,
        ui=SimpleNamespace(),
        ButtonStyle=SimpleNamespace(primary=1, secondary=2, gray=2, green=3, red=4),
    )
    monkeypatch.setitem(sys.modules, "discord", fake)
    monkeypatch.setitem(sys.modules, "discord.ext", fake_ext)
    monkeypatch.setitem(sys.modules, "discord.ext.commands", fake_commands)
    monkeypatch.setattr(de, "DISCORD_AVAILABLE", True)
    return fake


def _make_adapter_cls():
    class ConcreteAdapter(de.DiscordEnvelopeAdapter):
        async def send_message(self, chat_id, text, **kwargs):
            return None

    return ConcreteAdapter


def _adapter(**cfg):
    return _make_adapter_cls()({"token": "tok-123", **cfg})


# ---------- Bug 1: fire-and-forget bot task ----------

def test_start_stores_bot_task_and_stop_cancels_it(fake_discord):
    async def scenario():
        adapter = _adapter()
        await adapter.start(lambda msg: asyncio.sleep(0))
        task = adapter._bot_task
        assert task is not None, "bot task must be stored, not discarded"
        assert isinstance(task, asyncio.Task)
        assert not task.done()
        await asyncio.sleep(0)
        assert adapter._bot.token == "tok-123"

        await adapter.stop()
        assert task.done()
        assert task.cancelled()
        # sentinel was queued by stop() for the listener
        assert adapter._queue.get_nowait() is None
        assert adapter._bot.closed

    asyncio.run(scenario())


def test_stop_is_safe_without_start(fake_discord):
    async def scenario():
        adapter = _adapter()
        await adapter.stop()
        assert adapter.is_running is False

    asyncio.run(scenario())


# ---------- Bug 2: button auth + approval resolution ----------

def _make_interaction(user_id):
    edits = []

    async def edit_message(content=None, **kwargs):
        edits.append(content)

    resp = SimpleNamespace(edit_message=edit_message)
    inter = SimpleNamespace(user=SimpleNamespace(id=user_id), response=resp, edits=edits)
    return inter


@pytest.fixture
def approval_spy(monkeypatch):
    calls = {}

    async def fake_resolve(platform, user_id, mission_id, action="approve"):
        calls.update(platform=platform, user_id=user_id, mission_id=mission_id, action=action)
        verb = "approved" if action == "approve" else "rejected"
        return True, f"✅ {verb} {mission_id}"

    monkeypatch.setattr(de, "resolve_approval", fake_resolve)
    return calls


def test_button_authorized_user_resolves_approval(monkeypatch, approval_spy):
    monkeypatch.setattr(de, "is_user_authorized", lambda platform, uid: True)
    adapter = _adapter()
    cb = adapter._make_button_callback("perm:approve:MISSION-123")
    inter = _make_interaction("111")
    asyncio.run(cb(inter))

    assert approval_spy == {
        "platform": "discord",
        "user_id": "111",
        "mission_id": "MISSION-123",
        "action": "approve",
    }
    assert inter.edits == ["✅ approved MISSION-123"]
    assert adapter.metrics["callback_handled"] == 1


def test_button_reject_action_parsed(monkeypatch, approval_spy):
    monkeypatch.setattr(de, "is_user_authorized", lambda platform, uid: True)
    adapter = _adapter()
    cb = adapter._make_button_callback("perm:reject:MISSION-9")
    inter = _make_interaction("222")
    asyncio.run(cb(inter))

    assert approval_spy["action"] == "reject"
    assert approval_spy["mission_id"] == "MISSION-9"
    assert inter.edits == ["✅ rejected MISSION-9"]


def test_button_unauthorized_user_denied(monkeypatch, approval_spy):
    monkeypatch.setattr(de, "is_user_authorized", lambda platform, uid: False)
    adapter = _adapter()
    cb = adapter._make_button_callback("perm:approve:MISSION-123")
    inter = _make_interaction("666")
    asyncio.run(cb(inter))

    assert approval_spy == {}, "resolve_approval must NOT run for unauthorized users"
    assert len(inter.edits) == 1
    assert "not authorized" in inter.edits[0].lower()
    assert adapter.metrics["events_rejected"] == 1


# ---------- Bug 3: listen() sentinel shutdown ----------

def test_listen_yields_items_then_exits_on_sentinel():
    async def scenario():
        adapter = _adapter()
        item_a, item_b = object(), object()
        await adapter._queue.put(item_a)
        await adapter._queue.put(item_b)
        adapter.is_running = True

        collected = []
        agen = adapter.listen()
        collected.append(await agen.__anext__())
        collected.append(await agen.__anext__())
        assert collected == [item_a, item_b]

        await adapter._queue.put(None)  # sentinel posted like stop() does
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
        await agen.aclose()

    asyncio.run(scenario())


def test_stop_unblocks_blocked_listener():
    """A consumer already blocked in queue.get() wakes up and exits after stop()."""

    async def scenario():
        adapter = _adapter()
        adapter.is_running = True
        await adapter._queue.put(object())

        agen = adapter.listen()
        await agen.__anext__()  # consume one item; consumer now blocked on get()

        async def waiter():
            with pytest.raises(StopAsyncIteration):
                await agen.__anext__()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        await adapter.stop()  # posts sentinel + clears is_running
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
