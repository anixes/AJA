"""Regression tests for the Telegram photo/vision path.

Covers the two live failures from 2026-08-24:
1. orchestrator.chat crashed with AttributeError ('list' has no .lower())
   when the last message carried multimodal (image) content.
2. Copilot chat-completions rejects images ('image media type not
   supported'); image prompts must route to the Responses API.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    monkeypatch.setattr(
        "aja.gateway.orchestrator.completion",
        lambda **kw: {"content": "vision answer", "tool_calls": []},
    )
    return gw


def test_chat_survives_multimodal_last_message(monkeypatch):
    """Regression: 'list' object has no attribute 'lower' (orchestrator L245)."""
    gw = _make_bare_gateway(monkeypatch)
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what product is this, any advice?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            ],
        }
    ]
    response = asyncio.run(
        gw.chat("what product is this", chat_history=history, image_url="data:image/jpeg;base64,AAAA")
    )
    assert response == "vision answer"


def test_chat_time_query_with_image_does_not_crash(monkeypatch):
    """The auto-tool time fallback must tolerate multimodal content lists."""
    gw = _make_bare_gateway(monkeypatch)

    captured = {}

    def fake_completion(**kw):
        captured["prompt"] = kw.get("prompt")
        return {"content": "ok", "tool_calls": []}

    monkeypatch.setattr("aja.gateway.orchestrator.completion", fake_completion)
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what time is it now?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            ],
        }
    ]
    response = asyncio.run(gw.chat("what time is it now?", chat_history=history, image_url="data:image/jpeg;base64,AAAA"))
    assert response == "ok"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    async def text(self):
        return json.dumps(self._payload)

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.posted_urls = []

    def post(self, url, json=None, headers=None):
        self.posted_urls.append(url)
        self.last_payload = json
        return _FakeResponse(self._payload)


def test_copilot_image_prompt_routes_to_responses_api(monkeypatch):
    """Images must use the Responses API even for gpt-4o-mini (chat-completions rejects them)."""
    from aja.orchestration.gateway import LLMGateway

    gw = LLMGateway(provider="copilot", api_key="test-key")
    payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "I see a product"}]}]}
    fake_session = _FakeSession(payload)
    monkeypatch.setattr(gw, "_get_session", lambda: fake_session)

    messages = [
        {"role": "system", "content": "vision sys"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe this"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            ],
        },
    ]
    result = asyncio.run(gw.chat(model="gpt-4o-mini", prompt=messages, system="vision sys"))

    assert any(url.endswith("/v1/responses") for url in fake_session.posted_urls), (
        f"expected Responses API route, got: {fake_session.posted_urls}"
    )
    assert result == "I see a product"
