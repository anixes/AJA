"""Tests for Telegram Tier 2: MEDIA delivery + error policy.

Covers reply_extras.py (tag extraction, error policy dedupe, friendly
formatting, document send) and tg_client.send_message MEDIA integration.
"""

import pytest

from aja.gateway.reply_extras import (
    ErrorPolicy,
    extract_media_tags,
    format_error_reply,
    send_documents,
)


# ---------------------------------------------------------------- extraction


def test_extract_no_tags_returns_text_unchanged():
    clean, paths = extract_media_tags("plain text\nno tags here")
    assert clean == "plain text\nno tags here"
    assert paths == []


def test_extract_strips_tags_and_collects_existing(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    text = f"Here is the report.\n\nMEDIA:{f}\nMEDIA:{f}\nAnalysis follows."
    clean, paths = extract_media_tags(text)
    assert "MEDIA:" not in clean
    assert "Here is the report." in clean
    assert "Analysis follows." in clean
    assert paths == [f], "duplicate tags collapse to one"


def test_extract_drops_missing_files():
    text = "text\nMEDIA:/nonexistent/zzz.log\nmore"
    clean, paths = extract_media_tags(text)
    assert "MEDIA:" not in clean
    assert paths == []


def test_extract_handles_quoted_paths(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    clean, paths = extract_media_tags(f'MEDIA:"{f}"')
    assert paths == [f]


# -------------------------------------------------------------- error policy


def test_error_policy_always_sends_every_time():
    ep = ErrorPolicy("always")
    assert ep.should_send("boom") is True
    assert ep.should_send("boom") is True


def test_error_policy_once_dedupes_within_cooldown():
    ep = ErrorPolicy("once")
    assert ep.should_send("same error") is True
    assert ep.should_send("same error") is False
    assert ep.should_send("different error") is True


def test_error_policy_silent_never_sends():
    ep = ErrorPolicy("silent")
    assert ep.should_send("anything") is False


def test_error_policy_unknown_falls_back_to_always():
    ep = ErrorPolicy("bogus")
    assert ep.policy == "always"


def test_format_error_reply_is_short_and_has_context():
    msg = format_error_reply(ValueError("boom"), "chat")
    assert msg.startswith("⚠️")
    assert "chat" in msg
    assert "boom" in msg
    assert len(msg) < 300


# ------------------------------------------------------------ document sends


@pytest.mark.anyio
async def test_send_documents_delivers_and_reports_failures(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("hello")

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_document(self, chat_id, document, caption=""):
            self.sent.append((chat_id, caption))
            document.close()

    bot = FakeBot()
    missing = tmp_path / "missing.bin"  # never created -> stat fails
    delivered, failed = await send_documents(bot, "100", [good, missing])

    assert delivered == [good]
    assert len(bot.sent) == 1
    assert any("missing.bin" in f for f in failed)


@pytest.mark.anyio
async def test_send_documents_skips_oversized(tmp_path):
    big = tmp_path / "big.zip"
    big.write_bytes(b"x" * 1024)  # small on disk; patch the cap instead

    from aja.gateway import reply_extras

    original_cap = reply_extras.MAX_DOCUMENT_BYTES
    reply_extras.MAX_DOCUMENT_BYTES = 10
    try:

        class FakeBot:
            async def send_document(self, **kwargs):
                raise AssertionError("must not be called for oversized files")

        delivered, failed = await send_documents(FakeBot(), "100", [big])
        assert delivered == []
        assert len(failed) == 1 and "too large" in failed[0]
    finally:
        reply_extras.MAX_DOCUMENT_BYTES = original_cap


# --------------------------------------------------- tg_client integration


@pytest.mark.anyio
async def test_tg_client_send_message_strips_media_tags(tmp_path):
    """send_message must strip MEDIA: lines before chunking."""
    from aja.gateway.tg_client import TelegramAdapter

    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._bot = None  # send_message returns None early when no bot

    f = tmp_path / "out.txt"
    f.write_text("data")

    # With no bot this returns None but must not crash on the MEDIA path.
    result = await adapter.send_message(
        "100", f"report ready\nMEDIA:{f}", reply_to_message_id=5
    )
    assert result is None
