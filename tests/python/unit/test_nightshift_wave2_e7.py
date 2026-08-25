"""Wave-2 E7 regression tests: orchestrator tool-loop fallthrough,
vision error visibility, and session image-size cap."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aja.gateway.base import MessageEvent, MessageType
from aja.gateway.orchestrator import UnifiedGateway


def _make_bare_gateway(monkeypatch):
    """UnifiedGateway without the heavy __init__ (LanceDB, adapters, etc.)."""
    gw = UnifiedGateway.__new__(UnifiedGateway)
    gw.model_id = "copilot:gpt-4o-mini"
    gw.trajectory_manager = None
    gw.context_threshold = 4000
    gw.memory = MagicMock()
    gw.memory.add_activity = MagicMock()
    gw._open_gateway_warned = False
    return gw


def _make_event(text, media_urls=None, message_type=MessageType.TEXT):
    return MessageEvent(
        platform="telegram",
        chat_id="100",
        user_id="42",
        message_type=message_type,
        text=text,
        media_urls=media_urls or [],
        message_id="m1",
    )


def _stub_gateway_event_path(gw):
    """Stub everything handle_gateway_event touches except the logic under test."""
    gw._is_telegram_user_authorized = lambda event: True
    gw.gateway_state = MagicMock()
    gw.gateway_state.get_session.return_value = {"history": []}
    gw.telegram_adapter = MagicMock()
    gw.telegram_adapter.send_message = AsyncMock()
    return gw


def test_unregistered_dotted_tool_not_shell_executed(monkeypatch):
    """L3#4: advertised-but-unregistered tools (browser.*) must never fall
    through to ToolExecutor.execute as a shell command."""
    gw = _make_bare_gateway(monkeypatch)

    calls = {"n": 0}

    def fake_completion(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "name": "browser.click",
                        "arguments": '{"selector": "#submit"}',
                    }
                ],
            }
        return "done answering"

    monkeypatch.setattr("aja.gateway.orchestrator.completion", fake_completion)

    def _forbidden_execute(self, command, cwd=None, workspace_mode="direct"):
        raise AssertionError(
            f"executor.execute was invoked with tool name {command!r}"
        )

    monkeypatch.setattr(
        "aja.orchestration.tools.executor.ToolExecutor.execute",
        _forbidden_execute,
    )

    reply = asyncio.run(gw.chat("click the submit button"))
    assert calls["n"] == 2
    assert "done answering" == reply


def test_malformed_tool_args_note_returned_to_model(monkeypatch):
    """L3#5: malformed JSON arguments are debug-logged and surfaced back to
    the model instead of being silently coerced to {}."""
    gw = _make_bare_gateway(monkeypatch)
    seen_results = []

    calls = {"n": 0}

    def fake_completion(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "tool_calls": [
                    {"name": "sleep", "arguments": "{not valid json"}
                ],
            }
        # Second call carries the tool observations; capture them.
        seen_results.append(kw["prompt"][-1]["content"])
        return "recovered"

    monkeypatch.setattr("aja.gateway.orchestrator.completion", fake_completion)

    reply = asyncio.run(gw.chat("wait a moment"))
    assert reply == "recovered"
    assert any("invalid JSON" in r for r in seen_results)


def test_vision_provider_error_sends_visible_fallback(monkeypatch):
    """L4#2: a provider 400 on an image must never leave the user silent."""
    gw = _make_bare_gateway(monkeypatch)
    _stub_gateway_event_path(gw)
    gw.chat = AsyncMock(side_effect=Exception("400 Bad Request: image rejected"))

    event = _make_event(
        "what do you see?",
        media_urls=["data:image/jpeg;base64,AAAA"],
        message_type=MessageType.PHOTO,
    )
    asyncio.run(gw.handle_gateway_event(event))

    gw.telegram_adapter.send_message.assert_awaited_once()
    chat_id, msg = gw.telegram_adapter.send_message.await_args.args
    assert "couldn't analyze that image" in msg
    assert "/models" in msg


def test_oversized_image_not_persisted_in_session(monkeypatch):
    """L4#5: >4MB data URLs stay turn-local and are not written into
    session_json via last_image_url."""
    gw = _make_bare_gateway(monkeypatch)
    _stub_gateway_event_path(gw)
    gw.chat = AsyncMock(return_value="I see it")

    big_url = "data:image/jpeg;base64," + "A" * (4 * 1024 * 1024 + 16)
    event = _make_event(
        "what is this?",
        media_urls=[big_url],
        message_type=MessageType.PHOTO,
    )
    asyncio.run(gw.handle_gateway_event(event))

    persisted_sessions = [
        c.args[1] for c in gw.gateway_state.update_session.call_args_list
    ]
    assert persisted_sessions, "expected at least one update_session call"
    for sess in persisted_sessions:
        assert "last_image_url" not in sess
    # The image was still analyzed this turn.
    _, kwargs = gw.chat.await_args
    assert kwargs.get("image_url") == big_url
