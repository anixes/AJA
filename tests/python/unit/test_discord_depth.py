"""Tests for DiscordAdapter depth parity: approvals, telemetry pipeline,
vision attachments, lifecycle cleanup, and metrics snapshots.

discord.py is NOT installed in this environment; module-level globals
(``discord``, ``commands``, ``DISCORD_AVAILABLE``) are replaced with fakes so
the interactive-view code paths execute exactly as they would in production.
"""

import asyncio
import sys
import types

import pytest

import aja.gateway.adapters.discord_adapter as da
from aja.gateway.adapters.discord_adapter import DiscordAdapter


# --------------------------------------------------------------------- #
# Fake discord.py surface
# --------------------------------------------------------------------- #


class _FakeButtonStyle:
    success = "success"
    danger = "danger"


def _make_fake_discord():
    mod = types.ModuleType("discord")

    class _UI(types.ModuleType):
        pass

    ui_mod = types.ModuleType("discord.ui")

    class Button:
        def __init__(self, label=None, style=None, custom_id=None):
            self.label = label
            self.style = style
            self.custom_id = custom_id
            self.callback = None

    class View:
        def __init__(self, timeout=None):
            self.timeout = timeout
            self.children = []

        def add_item(self, item):
            self.children.append(item)

    ui_mod.Button = Button
    ui_mod.View = View
    mod.ui = ui_mod
    mod.ButtonStyle = _FakeButtonStyle()

    ext_mod = types.ModuleType("discord.ext")
    commands_mod = types.ModuleType("discord.ext.commands")

    class _Intents:
        def __init__(self):
            self.message_content = False

        @staticmethod
        def default():
            return _Intents()

    class _Bot:
        def __init__(self, command_prefix=None, intents=None):
            self.command_prefix = command_prefix
            self.intents = intents
            self.user = None

    commands_mod.Bot = _Bot
    commands_mod.Intents = _Intents
    ext_mod.commands = commands_mod
    mod.ext = ext_mod
    return mod


@pytest.fixture()
def fake_discord(monkeypatch):
    mod = _make_fake_discord()
    monkeypatch.setitem(sys.modules, "discord", mod)
    monkeypatch.setitem(sys.modules, "discord.ui", mod.ui)
    monkeypatch.setitem(sys.modules, "discord.ext", mod.ext)
    monkeypatch.setitem(sys.modules, "discord.ext.commands", mod.ext.commands)
    # Globals only exist when real discord.py was importable at module load.
    monkeypatch.setattr(da, "DISCORD_AVAILABLE", True, raising=False)
    monkeypatch.setattr(da, "discord", mod, raising=False)
    monkeypatch.setattr(da, "commands", mod.ext.commands, raising=False)
    return mod


# --------------------------------------------------------------------- #
# Approval interaction scenarios
# --------------------------------------------------------------------- #


class _FakeMemoryDBTable:
    def __init__(self, store):
        self._store = store

    def add(self, rows):
        self._store.extend(rows)
        return True


class _FakeMemory:
    """Mimics secretary memory: get_mission / update_mission / journal table."""

    def __init__(self, mission):
        self.mission = mission
        self.updates = []
        self.journal_rows = []

    def get_mission(self, mission_id):
        return self.mission

    def update_mission(self, mission_id, fields):
        self.updates.append((mission_id, dict(fields)))
        if isinstance(self.mission, dict):
            self.mission.update(fields)
        return True

    @property
    def db(self):
        return types.SimpleNamespace(
            open_table=lambda name: _FakeMemoryDBTable(self.journal_rows)
        )


@pytest.fixture()
def patch_memory(monkeypatch):
    import aja.memory.secretary as secretary_mod

    holder = {}

    def _install(mission):
        memory = _FakeMemory(mission)
        monkeypatch.setattr(secretary_mod, "get_aja_memory", lambda: memory)
        holder["memory"] = memory
        return memory

    holder["install"] = _install
    yield holder


def _interaction(user_id=111):
    obj = types.SimpleNamespace()
    obj.user = types.SimpleNamespace(id=user_id)
    edited = {}

    class _Response:
        async def edit_message(self, content=None, view=None):
            edited["content"] = content
            edited["view"] = view

    obj.response = _Response()
    obj.edited = edited
    return obj


def test_button_interaction_authorized_and_approved(
    monkeypatch, fake_discord, patch_memory
):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "111")
    patch_memory["install"](
        {"mission_id": "M-1", "status": "AWAITING_APPROVAL", "metadata_json": "{}"}
    )
    adapter = DiscordAdapter({"token": "tok"})
    interaction = _interaction(user_id=111)

    result = asyncio.run(
        adapter._handle_approval_interaction(interaction, "approve", "M-1")
    )

    assert "Approved" in result
    memory = patch_memory["memory"]
    assert memory.updates == [("M-1", {"status": "ACTIVE"})]
    assert [r["kind"] for r in memory.journal_rows] == ["NODE_APPROVED"]
    assert adapter.metrics["callback_handled"] == 1

    # Full interaction path edits the original message with the outcome.
    # Reinstall a pending mission since the previous approve transitioned it.
    patch_memory["install"](
        {"mission_id": "M-1", "status": "AWAITING_APPROVAL", "metadata_json": "{}"}
    )
    interaction.data = {"custom_id": "aja:approve:M-1"}
    asyncio.run(adapter._on_discord_interaction(interaction))
    assert "Approved" in interaction.edited["content"]
    assert interaction.edited["view"] is None


def test_button_interaction_rejected_path(monkeypatch, fake_discord, patch_memory):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "111")
    patch_memory["install"](
        {"mission_id": "M-2", "status": "AWAITING_APPROVAL", "metadata_json": "{}"}
    )
    adapter = DiscordAdapter({"token": "tok"})
    interaction = _interaction(user_id=111)

    result = asyncio.run(
        adapter._handle_approval_interaction(interaction, "reject", "M-2")
    )

    assert "Rejected" in result
    memory = patch_memory["memory"]
    assert memory.updates == [("M-2", {"status": "REJECTED"})]
    assert [r["kind"] for r in memory.journal_rows] == ["NODE_REJECTED"]


def test_button_interaction_unauthorized(monkeypatch, fake_discord, patch_memory):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "111")
    install = patch_memory["install"]

    # Install nothing: resolver must never be reached.
    adapter = DiscordAdapter({"token": "tok"})
    interaction = _interaction(user_id=999)
    interaction.data = {"custom_id": "aja:approve:M-3"}

    asyncio.run(adapter._on_discord_interaction(interaction))

    assert "Unauthorized" in interaction.edited["content"]
    assert patch_memory.get("memory") is None
    # Malformed/unknown widget ids are ignored entirely.
    stranger = _interaction(user_id=111)
    stranger.data = {"custom_id": "some_other_widget"}
    asyncio.run(adapter._on_discord_interaction(stranger))
    assert not stranger.edited


def test_button_interaction_expired(monkeypatch, fake_discord, patch_memory):
    import json

    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "111")
    patch_memory["install"](
        {
            "mission_id": "M-4",
            "status": "AWAITING_APPROVAL",
            "metadata_json": json.dumps({"expires_at": "2000-01-01T00:00:00+00:00"}),
        }
    )
    adapter = DiscordAdapter({"token": "tok"})
    interaction = _interaction(user_id=111)

    result = asyncio.run(
        adapter._handle_approval_interaction(interaction, "approve", "M-4")
    )

    assert "expired" in result.lower()
    assert patch_memory["memory"].updates == []


def test_button_interaction_already_handled(monkeypatch, fake_discord, patch_memory):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "111")
    patch_memory["install"](
        {"mission_id": "M-5", "status": "ACTIVE", "metadata_json": "{}"}
    )
    adapter = DiscordAdapter({"token": "tok"})
    interaction = _interaction(user_id=111)

    result = asyncio.run(
        adapter._handle_approval_interaction(interaction, "approve", "M-5")
    )

    assert "already handled" in result.lower()
    assert patch_memory["memory"].updates == []


def test_approval_view_builds_persistent_buttons(fake_discord):
    adapter = DiscordAdapter({"token": "tok"})
    view = adapter._build_approval_view("M-9")

    assert view.timeout is None
    custom_ids = [child.custom_id for child in view.children]
    assert custom_ids == ["aja:approve:M-9", "aja:reject:M-9"]


# --------------------------------------------------------------------- #
# Telemetry pipeline
# --------------------------------------------------------------------- #


def test_telemetry_fans_out_to_all_channel_queues(fake_discord):
    adapter = DiscordAdapter({"token": "tok"})

    async def scenario():
        adapter.is_running = True
        # Tail tasks consume their channel queues; record deliveries instead
        # of asserting on queue contents.
        delivered = []

        async def _record(chat_id, text, view=None, **kwargs):
            delivered.append((chat_id, text))

        adapter.send_message = _record
        adapter.start_tail("chan-1")
        adapter.start_tail("chan-2")
        await asyncio.sleep(0)

        ev = {
            "event_id": "e1",
            "kind": "MISSION_DONE",
            "target": "m-1",
            "status": "SUCCESS",
            "message": "done",
            "command": "",
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        adapter._put_telemetry(ev)
        for _ in range(50):
            if len(delivered) >= 2:
                break
            await asyncio.sleep(0.01)

        assert ("chan-1", "[SUCCESS] done") in delivered
        assert ("chan-2", "[SUCCESS] done") in delivered
        # Same event reached BOTH channels (single dispatcher fan-out).
        assert adapter.telemetry_queue.empty()
        await adapter.stop_tails()
        assert adapter._tail_tasks == {}
        assert adapter._chat_queues == {}

    asyncio.run(scenario())


def test_telemetry_queue_drop_oldest_keeps_approvals(fake_discord):
    adapter = DiscordAdapter({"token": "tok"})
    adapter.telemetry_queue = asyncio.Queue(maxsize=2)

    filler = {"event_id": "f", "kind": "INFO", "message": "x"}
    adapter._put_telemetry(filler)
    adapter._put_telemetry(dict(filler, event_id="f2"))
    approval = {"event_id": "a", "kind": "AWAITING_APPROVAL", "target": "m", "message": "?"}
    adapter._put_telemetry(approval)  # must never be dropped

    kinds = [
        adapter.telemetry_queue.get_nowait()["kind"] for _ in range(adapter.telemetry_queue.qsize())
    ]
    assert "AWAITING_APPROVAL" in kinds


def test_stop_cancels_background_tasks_and_unsubscribes(fake_discord):
    from aja.runtime.event_bus import bus

    adapter = DiscordAdapter({"token": "tok"})

    class _CloseableBot:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    async def sleeper():
        await asyncio.sleep(3600)

    async def scenario():
        bot = _CloseableBot()
        adapter._bot = bot
        adapter.is_running = True
        adapter._poll_task = asyncio.create_task(sleeper())
        adapter._dispatcher_task = asyncio.create_task(sleeper())
        adapter._tail_tasks["chan"] = asyncio.create_task(sleeper())
        adapter._bot_task = asyncio.create_task(sleeper())

        seen = []
        handler = lambda payload: seen.append(payload)  # noqa: E731
        bus.subscribe("TEST_DISCORD_DEPTH_EVT", handler)
        adapter._bus_handlers.append(("TEST_DISCORD_DEPTH_EVT", handler))

        await adapter.stop()

        for task in (
            adapter._poll_task,
            adapter._dispatcher_task,
            adapter._bot_task,
        ):
            assert task is None  # awaited + cleared
        assert adapter._tail_tasks == {}
        assert adapter._bus_handlers == []
        assert bot.closed is True
        assert adapter.is_running is False
        bus.publish("TEST_DISCORD_DEPTH_EVT", {})
        await asyncio.sleep(0)
        assert seen == []  # unsubscribed

    asyncio.run(scenario())


# --------------------------------------------------------------------- #
# Vision input
# --------------------------------------------------------------------- #


class _FakeAttachment:
    def __init__(self, content_type="image/png"):
        self.content_type = content_type

    async def read(self):
        return b"\x89PNG-fake-bytes"


def _fake_message(author_id=555, content="", attachments=None):
    msg = types.SimpleNamespace()
    msg.author = types.SimpleNamespace(id=author_id, bot=False)
    msg.channel = types.SimpleNamespace(id=987)
    msg.id = 42
    msg.content = content
    msg.attachments = attachments or []

    async def reply(text):
        msg.replied = text

    msg.reply = reply
    return msg


def test_attachments_convert_to_base64_data_urls(monkeypatch, fake_discord):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "555")
    adapter = DiscordAdapter({"token": "tok"})
    message = _fake_message(attachments=[_FakeAttachment()])

    event = asyncio.run(adapter._on_discord_message(message))

    assert event.platform == "discord"
    assert event.message_type.value == "photo"
    assert len(event.media_urls) == 1
    url = event.media_urls[0]
    assert url.startswith("data:image/png;base64,")
    import base64

    base64.b64decode(url.split(",", 1)[1], validate=True)
    assert "image" in event.text.lower()


def test_non_image_attachment_ignored_and_text_only_event(monkeypatch, fake_discord):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "555")
    adapter = DiscordAdapter({"token": "tok"})
    message = _fake_message(content="hello", attachments=[_FakeAttachment("text/plain")])

    event = asyncio.run(adapter._on_discord_message(message))

    assert event.message_type.value == "text"
    assert event.media_urls == []
    assert event.text == "hello"


def test_unauthorized_message_rejected_with_notice(monkeypatch, fake_discord):
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "555")
    adapter = DiscordAdapter({"token": "tok"})
    message = _fake_message(author_id=999, content="sneak")

    event = asyncio.run(adapter._on_discord_message(message))

    assert event is None
    assert adapter.metrics["events_rejected"] == 1
    assert "Access Denied" in message.replied


# --------------------------------------------------------------------- #
# Metrics & health snapshot
# --------------------------------------------------------------------- #


def test_health_snapshot_shape(fake_discord):
    adapter = DiscordAdapter({"token": "tok"})
    snapshot = adapter.get_health_snapshot()

    assert snapshot["adapter"] == "discord"
    assert snapshot["is_running"] is False
    expected_keys = {
        "events_received",
        "events_rejected",
        "events_dequeued",
        "messages_sent",
        "send_failures",
        "poll_retries",
        "callback_handled",
        "last_error",
        "last_error_at",
        "queue_lag_seconds",
        "queue_size",
    }
    assert expected_keys.issubset(snapshot.keys())
