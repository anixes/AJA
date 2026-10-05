import asyncio
import os
import threading
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
import pytest

from aja.presence.notifier import _send_telegram
from aja.gateway.audio_transcriber import _get_local_whisper_model, _WHISPER_LOCK
import aja.gateway.audio_transcriber as transcriber_mod
from aja.orchestration.context_window import compress_history


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


def test_whisper_concurrency_lock():
    # Reset model to None to test lazy loading under lock
    orig_model = transcriber_mod._LOCAL_WHISPER_MODEL
    transcriber_mod._LOCAL_WHISPER_MODEL = None

    loaded_instances = []

    def mock_load_model(name):
        import time
        time.sleep(0.02)
        inst = MagicMock(name=f"whisper_{name}")
        loaded_instances.append(inst)
        return inst

    try:
        with patch.dict("sys.modules", {"whisper": MagicMock(load_model=mock_load_model)}):
            threads = []
            results = []

            def worker():
                m = _get_local_whisper_model()
                results.append(m)

            for _ in range(5):
                t = threading.Thread(target=worker)
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            # Ensure all threads received the same model instance and load_model was called exactly once
            assert len(loaded_instances) == 1
            for r in results:
                assert r is loaded_instances[0]
    finally:
        transcriber_mod._LOCAL_WHISPER_MODEL = orig_model


def test_context_window_compress_history_safe_halt():
    # Test that compress_history prunes when over limit and halts gracefully
    history = [
        {"role": "user", "content": "Initial prompt " + "x" * 200},
        {"role": "assistant", "content": "Response 1 " + "y" * 200},
        {"role": "user", "content": "Follow-up question " + "z" * 200},
        {"role": "assistant", "content": "Response 2 " + "w" * 200},
    ]

    compress_history(history, limit=50, history_floor=10)
    # History must have been pruned safely without infinite looping
    assert len(history) <= 4
