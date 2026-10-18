"""Unit tests for notification delivery and resilience."""

import os
from unittest.mock import MagicMock, patch
import pytest

from aja.presence.notifier import _send_telegram


def test_notifier_telegram_markdown_fallback_on_400(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999888")

    with patch("requests.post") as mock_post:
        # First call (Markdown) returns 400 (e.g. unescaped markdown entity)
        resp_400 = MagicMock()
        resp_400.status_code = 400
        
        # Second call (plain text fallback) returns 200
        resp_200 = MagicMock()
        resp_200.status_code = 200

        mock_post.side_effect = [resp_400, resp_200]

        _send_telegram("Message with unbalanced *bold _markdown")

        assert mock_post.call_count == 2
        
        # Call 1: tried with parse_mode=Markdown
        first_call_payload = mock_post.call_args_list[0][1]["json"]
        assert first_call_payload["parse_mode"] == "Markdown"
        assert first_call_payload["chat_id"] == "999888"

        # Call 2: fallback with plain text without parse_mode
        second_call_payload = mock_post.call_args_list[1][1]["json"]
        assert "parse_mode" not in second_call_payload
        assert second_call_payload["chat_id"] == "999888"
        assert "Message with unbalanced *bold _markdown" in second_call_payload["text"]


def test_notifier_telegram_comma_separated_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111, 222, 333")

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        _send_telegram("Hello test")

        assert mock_post.call_count == 1
        payload = mock_post.call_args[1]["json"]
        # Must pick first chat id stripped
        assert payload["chat_id"] == "111"
