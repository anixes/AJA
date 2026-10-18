"""Telegram Tier 1 UX tests: ack reactions, StatusBubble, command menu."""
from types import SimpleNamespace

import pytest

import aja.gateway.tg_client as tg_client
from aja.gateway.telegram_menu import CORE_COMMANDS, register_command_menu


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.deletes = []
        self.reactions = []
        self.commands = None

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

    async def set_my_commands(self, commands):
        self.commands = commands


class ExplodingBot(FakeBot):
    async def set_message_reaction(self, *a, **k):
        raise RuntimeError("reactions not allowed in this group")

    async def send_message(self, *a, **k):
        raise RuntimeError("send failed")


# ----------------------------------------------------------------- StatusBubble


@pytest.mark.anyio
async def test_status_bubble_start_sends_silent_working_message():
    bot = FakeBot()
    bubble = tg_client.StatusBubble(bot, "42")
    await bubble.start()
    assert len(bot.sent) == 1
    assert bot.sent[0].text == tg_client.STATUS_BUBBLE_INITIAL_TEXT
    assert bot.sent[0].disable_notification is True
    assert bubble.message_id == 101
    assert bubble.active


@pytest.mark.anyio
async def test_status_bubble_update_edits_same_message():
    bot = FakeBot()
    bubble = tg_client.StatusBubble(bot, "42")
    await bubble.start()
    await bubble.update("🔧 Running tool: ls")
    assert bot.edits == [("42", 101, "🔧 Running tool: ls", {"disable_notification": True})]
    assert not bot.deletes


@pytest.mark.anyio
async def test_status_bubble_finalize_edits_to_final_text():
    bot = FakeBot()
    bubble = tg_client.StatusBubble(bot, "42")
    await bubble.start()
    await bubble.finalize("All done!")
    assert bot.edits[-1][:3] == ("42", 101, "All done!")
    assert not bot.deletes
    assert not bubble.active


@pytest.mark.anyio
async def test_status_bubble_finalize_without_text_deletes_bubble():
    bot = FakeBot()
    bubble = tg_client.StatusBubble(bot, "42")
    await bubble.start()
    await bubble.finalize(None)
    assert bot.deletes == [("42", 101)]
    assert not bubble.active


@pytest.mark.anyio
async def test_status_bubble_degrades_silently_on_bot_failure():
    bot = ExplodingBot()
    bubble = tg_client.StatusBubble(bot, "42")
    await bubble.start()  # send fails -> inactive
    assert not bubble.active
    # update/finalize on an inactive bubble are no-ops (no exception).
    await bubble.update("x")
    await bubble.finalize("y")
    assert bot.edits == []


# ---------------------------------------------------------------- menu registration


@pytest.mark.anyio
async def test_register_command_menu_payload_shape():
    bot = FakeBot()
    assert await register_command_menu(bot) is True
    assert bot.commands is not None
    names = [c["command"] for c in bot.commands]
    assert names == ["start", "help", "status", "kanban", "missions", "models", "local", "doctor", "clear"]
    for c in bot.commands:
        assert isinstance(c["description"], str) and c["description"]
        assert "\n" not in c["description"]
    assert len(names) == len(CORE_COMMANDS)


@pytest.mark.anyio
async def test_register_command_menu_tolerates_failure():
    class BadCommandsBot(FakeBot):
        async def set_my_commands(self, commands):
            raise RuntimeError("api down")

    assert await register_command_menu(BadCommandsBot()) is False
    assert await register_command_menu(None) is False


# ---------------------------------------------------------------- ack reactions


@pytest.mark.anyio
async def test_ack_reaction_applied_on_authorized_message():
    bot = FakeBot()
    ok = await tg_client._safe_set_reaction(bot, "100", 555, tg_client.ACK_REACTION_EMOJI)
    assert ok is True
    assert bot.reactions[0][0] == "100"
    assert bot.reactions[0][1] == 555


@pytest.mark.anyio
async def test_ack_reaction_degrades_gracefully_when_bot_raises():
    bot = ExplodingBot()
    ok = await tg_client._safe_set_reaction(bot, "100", 555, tg_client.ACK_REACTION_EMOJI)
    assert ok is False  # no exception propagated


@pytest.mark.anyio
async def test_ack_reaction_degrades_when_api_missing():
    bot = SimpleNamespace()  # no set_message_reaction attribute at all
    ok = await tg_client._safe_set_reaction(bot, "100", 555, "👀")
    assert ok is False


@pytest.mark.anyio
async def test_done_reaction_fallback_chain():
    """✅ first; if rejected (returns False), 👍 fallback attempted."""
    attempts = []

    class PickyBot(FakeBot):
        async def set_message_reaction(self, chat_id, message_id, reaction, **kwargs):
            emoji = getattr(reaction[0], "emoji", reaction[0])
            attempts.append(emoji)
            if emoji == "✅":
                raise RuntimeError("invalid reaction emoji")
            return None

    bot = PickyBot()
    if not await tg_client._safe_set_reaction(bot, "1", 2, tg_client.DONE_REACTION_EMOJI):
        await tg_client._safe_set_reaction(bot, "1", 2, tg_client.DONE_REACTION_FALLBACK_EMOJI)
    assert attempts == ["✅", "👍"]
