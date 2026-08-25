"""Wave-2 E4 regression tests: Telegram streaming resilience + 4096 guards.

Covers:
- tg_client.send_message split-and-send for >4096 replies (was silently dropped)
- tg_client photo download 20 MiB getFile cap guard
- telegram_envelope._handle_stream_chunk: RetryAfter respect, terminal-error
  finalization, circuit breaker, 4096 edit cap, state pruning/TTL
- streaming edits stay plain-text (no parse_mode) to avoid parse-entity 400s
"""
import pytest

from aja.gateway.adapters.telegram_envelope import (
    STREAM_MAX_CONSECUTIVE_ERRORS,
    TelegramEnvelopeAdapter,
    _cap_stream_text,
)
from aja.gateway.tg_client import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramAdapter,
    split_for_telegram,
)
from aja.messaging.envelope import Envelope


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


class FakeBot:
    def __init__(self, edit_error=None, send_error=None, raise_once=False):
        self.sent = []
        self.edits = []
        self.edit_calls = 0
        self.next_message_id = 900
        self.edit_error = edit_error
        self.send_error = send_error
        self.raise_once = raise_once

    async def send_message(self, chat_id, text, **kwargs):
        if self.send_error is not None:
            err = self.send_error
            if self.raise_once:
                self.send_error = None
            raise err
        result = type("Msg", (), {"message_id": self.next_message_id})()
        self.next_message_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return result

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, **kwargs):
        self.edit_calls += 1
        if self.edit_error is not None:
            err = self.edit_error
            if self.raise_once:
                self.edit_error = None
            raise err
        self.edits.append({"text": text, "chat_id": chat_id, "message_id": message_id})
        return True


def make_envelope_adapter(monkeypatch, bot):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "42")
    adapter = TelegramEnvelopeAdapter({"token": "test-token"})
    adapter._bot = bot
    clock = {"t": 100.0}
    adapter._clock = lambda: clock["t"]
    return adapter, clock


# ---------------------------------------------------------------------------
# Fix 1: split-and-send of long final replies in tg_client
# ---------------------------------------------------------------------------


class _FakeTGBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return type("Msg", (), {"message_id": len(self.sent)})()


def _make_tg_adapter():
    adapter = TelegramAdapter({"token": "test-token"})
    adapter._bot = _FakeTGBot()
    return adapter


def test_split_for_telegram_short_text_single_chunk():
    assert split_for_telegram("hello") == ["hello"]


def test_split_for_telegram_prefers_newline_boundary():
    head = "a" * 4000
    tail = "b" * 2000
    text = f"{head}\n{tail}"
    chunks = split_for_telegram(text)
    assert all(len(c) <= TELEGRAM_MESSAGE_LIMIT for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
    # First chunk should break at the newline, not mid-word.
    assert chunks[0] == head


def test_split_for_telegram_falls_back_to_space_then_hard_cut():
    text = ("x" * 300 + " ") * 30  # spaces every 301 chars
    chunks = split_for_telegram(text)
    assert all(len(c) <= TELEGRAM_MESSAGE_LIMIT for c in chunks)
    assert "".join(c.replace(" ", "") for c in chunks) == text.replace(" ", "")


def test_split_for_telegram_no_boundary_hard_cut():
    text = "y" * 10_000
    chunks = split_for_telegram(text)
    assert [len(c) for c in chunks] == [4096, 4096, 1808]
    assert "".join(chunks) == text


@pytest.mark.anyio
async def test_send_message_splits_long_reply_into_chunks():
    adapter = _make_tg_adapter()
    long_reply = ("line of report output\n" * 600)[:12_000]
    result = await adapter.send_message("100", long_reply)
    sent = adapter._bot.sent
    assert len(sent) >= 3
    assert all(len(msg["text"]) <= TELEGRAM_MESSAGE_LIMIT for msg in sent)
    assert "".join(msg["text"] for msg in sent) == long_reply
    assert result is not None
    assert adapter.metrics["messages_sent"] == len(sent)


@pytest.mark.anyio
async def test_send_message_attaches_markup_only_to_final_chunk():
    markup = object()
    adapter = _make_tg_adapter()
    await adapter.send_message("100", "z" * 9000, reply_markup=markup)
    sent = adapter._bot.sent
    assert len(sent) >= 2
    assert [msg.get("reply_markup") for msg in sent[:-1]] == [None] * (len(sent) - 1)
    assert sent[-1]["reply_markup"] is markup


@pytest.mark.anyio
async def test_send_message_short_reply_untouched():
    adapter = _make_tg_adapter()
    await adapter.send_message("100", "short reply")
    assert len(adapter._bot.sent) == 1
    assert adapter._bot.sent[0]["text"] == "short reply"


# ---------------------------------------------------------------------------
# Fix 5: 20 MiB getFile cap on photo downloads
# ---------------------------------------------------------------------------


class _FakePhotoSize:
    def __init__(self, file_id, file_size=None):
        self.file_id = file_id
        self.file_size = file_size


class _FakeTGUser:
    def __init__(self):
        self.id = 42


class _FakeTGMessage:
    def __init__(self, photos=None, text=""):
        self.chat_id = 100
        self.chat = type("Chat", (), {"id": 100})()
        self.from_user = _FakeTGUser()
        self.text = text
        self.caption = None
        self.photo = photos
        self.message_id = 555


class _FakeTGUpdate:
    def __init__(self, message):
        self.message = message


class _NoDownloadContext:
    class _Bot:
        async def get_file(self, file_id):  # pragma: no cover - must not run
            raise AssertionError("get_file must not be called for oversized photo")

    bot = _Bot()


@pytest.mark.anyio
async def test_oversized_photo_rejected_with_user_visible_notice():
    adapter = _make_tg_adapter()
    oversized = _FakePhotoSize("big-photo", file_size=21 * 1024 * 1024)
    update = _FakeTGUpdate(_FakeTGMessage(photos=[oversized], text="what is this?"))
    await adapter._handle_message(update, _NoDownloadContext())
    # No media captured, no crash, and the user got a visible explanation.
    assert adapter._queue.qsize() == 1
    notice_sent = [m for m in adapter._bot.sent if "too large" in m["text"]]
    assert notice_sent, "expected a user-visible oversized-image notice"


@pytest.mark.anyio
async def test_normal_photo_download_still_works():
    import base64

    class FakeFile:
        async def download_as_bytearray(self):
            return bytearray(b"imgbytes")

    class Ctx:
        class Bot:
            async def get_file(self, file_id):
                assert file_id == "photo-ok"
                return FakeFile()

        bot = Bot()

    adapter = _make_tg_adapter()
    small = _FakePhotoSize("photo-ok", file_size=1024)
    update = _FakeTGUpdate(_FakeTGMessage(photos=[small], text=""))
    await adapter._handle_message(update, Ctx())
    event = adapter._queue.get_nowait()
    assert event.media_urls, "normal-size photo must still be downloaded"
    assert base64.b64decode(event.media_urls[0].split(",", 1)[1]) == b"imgbytes"


# ---------------------------------------------------------------------------
# Fixes 2/3/4: envelope stream chunk hardening
# ---------------------------------------------------------------------------


class _RetryAfter(Exception):
    def __init__(self, retry_after):
        super().__init__(f"Retry after {retry_after}")
        self.retry_after = retry_after


@pytest.mark.anyio
async def test_cap_stream_text_truncates_with_marker():
    capped = _cap_stream_text("x" * 5000)
    assert len(capped) <= 4096
    assert capped.endswith("[truncated]")
    assert _cap_stream_text("short") == "short"


@pytest.mark.anyio
async def test_stream_edit_capped_at_4096(monkeypatch):
    bot = FakeBot()
    adapter, clock = make_envelope_adapter(monkeypatch, bot)
    env = Envelope(surface="telegram", chat_id="100", correlation_id="conv-cap")
    await adapter.send_envelope(env.stream_chunk("a" * 4000))
    clock["t"] += 1.5
    await adapter.send_envelope(env.stream_chunk("b" * 2000))
    assert len(bot.edits) == 1
    edited = bot.edits[0]["text"]
    assert len(edited) <= 4096
    assert "[truncated]" in edited


@pytest.mark.anyio
async def test_retry_after_sleeps_requested_seconds_once(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(
        "aja.gateway.adapters.telegram_envelope.asyncio.sleep", fake_sleep
    )
    bot = FakeBot(edit_error=_RetryAfter(7), raise_once=True)
    adapter, clock = make_envelope_adapter(monkeypatch, bot)
    env = Envelope(surface="telegram", chat_id="100", correlation_id="conv-429")
    await adapter.send_envelope(env.stream_chunk("Hello"))
    clock["t"] += 1.5
    result = await adapter.send_envelope(env.stream_chunk(" world"))
    # The requested interval was honored exactly once, then the retry succeeded.
    assert sleeps == [7.0]
    assert result is not None
    assert bot.edits[-1]["text"] == "Hello world"


@pytest.mark.anyio
async def test_not_modified_and_not_found_are_terminal(monkeypatch):
    class NotFoundErr(Exception):
        pass

    bot = FakeBot(edit_error=NotFoundErr("BadRequest: Message to edit not found"))
    adapter, clock = make_envelope_adapter(monkeypatch, bot)
    env = Envelope(surface="telegram", chat_id="100", correlation_id="conv-gone")
    await adapter.send_envelope(env.stream_chunk("Hello"))
    clock["t"] += 1.5
    await adapter.send_envelope(env.stream_chunk(" world"))
    assert adapter._stream_states == {}
    # Next chunk starts a FRESH message instead of editing a ghost.
    await adapter.send_envelope(env.stream_chunk(" fresh"))
    assert len(bot.sent) == 2
    assert bot.sent[-1]["text"] == " fresh"


@pytest.mark.anyio
async def test_not_modified_error_also_terminal(monkeypatch):
    class NotModifiedErr(Exception):
        pass

    bot = FakeBot(edit_error=NotModifiedErr("Bad Request: message is not modified"))
    adapter, clock = make_envelope_adapter(monkeypatch, bot)
    env = Envelope(surface="telegram", chat_id="100", correlation_id="conv-same")
    await adapter.send_envelope(env.stream_chunk("Hello"))
    clock["t"] += 1.5
    await adapter.send_envelope(env.stream_chunk(""))
    assert adapter._stream_states == {}


@pytest.mark.anyio
async def test_circuit_breaker_sends_fresh_final_message(monkeypatch):
    class TransientErr(Exception):
        pass

    bot = FakeBot(edit_error=TransientErr("telegram server exploded"))
    adapter, clock = make_envelope_adapter(monkeypatch, bot)
    env = Envelope(surface="telegram", chat_id="100", correlation_id="conv-brk")
    await adapter.send_envelope(env.stream_chunk("Hello"))
    for i in range(STREAM_MAX_CONSECUTIVE_ERRORS):
        clock["t"] += 1.5
        await adapter.send_envelope(env.stream_chunk(f" chunk{i}"))
    # After 3 consecutive failures: state finalized, one fresh fallback send.
    assert adapter._stream_states == {}
    assert len(bot.sent) == 2
    assert bot.sent[1]["text"].startswith("Hello")
    assert bot.sent[1]["text"].endswith("chunk2")
    # Further chunks start over with a new message rather than retry-editing.
    clock["t"] += 1.5
    await adapter.send_envelope(env.stream_chunk(" after"))
    assert len(bot.sent) == 3


@pytest.mark.anyio
async def test_states_pruned_on_ttl_sweep(monkeypatch):
    bot = FakeBot()
    adapter, clock = make_envelope_adapter(monkeypatch, bot)
    env = Envelope(surface="telegram", chat_id="100", correlation_id="conv-ttl")
    await adapter.send_envelope(env.stream_chunk("Hello"))
    assert "conv-ttl" in adapter._stream_states
    from aja.gateway.adapters.telegram_envelope import STREAM_STATE_TTL_SECONDS

    clock["t"] += STREAM_STATE_TTL_SECONDS + 1
    await adapter.send_envelope(env.stream_chunk("new turn"))
    # Aged state was swept: the new chunk started a fresh message.
    assert len(bot.sent) == 2
    assert bot.sent[1]["text"] == "new turn"


@pytest.mark.anyio
async def test_correlation_key_prevents_cross_turn_bleed(monkeypatch):
    bot = FakeBot()
    adapter, clock = make_envelope_adapter(monkeypatch, bot)
    turn_a = Envelope(surface="telegram", chat_id="100", correlation_id="turn-a")
    turn_b = Envelope(surface="telegram", chat_id="100", correlation_id="turn-b")
    await adapter.send_envelope(turn_a.stream_chunk("answer A"))
    await adapter.send_envelope(turn_b.stream_chunk("answer B"))
    # Two independent streams -> two messages, no cross-turn edits.
    assert len(bot.sent) == 2
    assert bot.edits == []


@pytest.mark.anyio
async def test_stream_edits_stay_plain_text_no_parse_mode(monkeypatch):
    """Fix 6 contract: streaming edits never carry parse_mode (partial LLM
    markdown would 400 on MarkdownV2 entity parsing)."""
    bot = FakeBot()
    adapter, clock = make_envelope_adapter(monkeypatch, bot)
    env = Envelope(
        surface="telegram",
        chat_id="100",
        correlation_id="conv-md",
        meta={"markdown": True},
    )
    await adapter.send_envelope(env.stream_chunk("**bold** | a | b |\n| - | - |"))
    clock["t"] += 1.5
    await adapter.send_envelope(env.stream_chunk(" more"))
    assert len(bot.sent) == 1
    assert "parse_mode" not in bot.sent[0]
    assert len(bot.edits) == 1
    assert "parse_mode" not in bot.edits[0]


@pytest.mark.anyio
async def test_breaker_fallback_send_failure_prunes_state(monkeypatch):
    class AlwaysErr(Exception):
        pass

    bot = FakeBot()
    adapter, clock = make_envelope_adapter(monkeypatch, bot)
    env = Envelope(surface="telegram", chat_id="100", correlation_id="conv-dead")
    await adapter.send_envelope(env.stream_chunk("Hello"))
    bot.edit_error = AlwaysErr("nope")
    for i in range(STREAM_MAX_CONSECUTIVE_ERRORS):
        clock["t"] += 1.5
        await adapter.send_envelope(env.stream_chunk(f" chunk{i}"))
    assert adapter._stream_states == {}
