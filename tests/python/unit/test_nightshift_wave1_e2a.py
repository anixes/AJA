"""Night-shift Wave 1 E2a regression tests.

Covers T2 findings F1/F2/F3/F6:
- resolve_provider_model(None) crash on `"planner": null` aja.json configs
- Gemini safety-blocked empty-candidates IndexError (gateway + google_adapter)
- OpenAI-format empty choices IndexError (gateway legacy + openai_compat)
- Multimodal/tool-role history serialized verbatim into Gemini text parts
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aja.llm import resolve_provider_model
from aja.orchestration.gateway import LLMGateway, _flatten_google_content
from aja.orchestration.providers.base import LLMResponse
from aja.orchestration.providers.google_adapter import GoogleAdapter
from aja.orchestration.providers.openai_compat import OpenAICompatAdapter


# ---------------------------------------------------------------------------
# F1: null / empty planner model must fall back to the default, not TypeError
# ---------------------------------------------------------------------------


class TestResolveProviderModelNoneGuard:
    def test_none_falls_back_to_default(self):
        """aja.json `"planner": null` yields None from raw json.load readers."""
        provider, model = resolve_provider_model(None, "hybrid", "lfm", "gemini-2.5-flash")
        assert provider == "google"
        assert model == "gemini-2.5-flash"

    def test_empty_string_falls_back_to_default(self):
        provider, model = resolve_provider_model("", "hybrid", "lfm", "gemini-2.5-flash")
        assert (provider, model) == ("google", "gemini-2.5-flash")

    def test_null_config_read_simulates_planner_null(self):
        """Simulate the raw json.load read path: explicit null defeats .get default."""
        cfg = {"swarm_settings": {"models": {"planner": None}}}
        model = cfg.get("swarm_settings", {}).get("models", {}).get(
            "planner", "google:gemini-2.5-flash"
        )
        assert model is None  # documents why the guard exists
        provider, resolved = resolve_provider_model(model, "hybrid", "lfm", "unused")
        assert provider == "google"
        assert resolved

    def test_valid_explicit_selection_still_wins(self):
        provider, model = resolve_provider_model(
            "copilot:gpt-4o-mini", "hybrid", "lfm", "gemini-2.5-flash"
        )
        assert (provider, model) == ("copilot", "gpt-4o-mini")


# ---------------------------------------------------------------------------
# F2: Gemini safety-blocked responses carry "candidates": [] -> IndexError
# ---------------------------------------------------------------------------


class TestGoogleEmptyCandidates:
    def test_adapter_parse_response_empty_candidates(self):
        resp = GoogleAdapter._parse_response(
            {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}},
            tools_was_provided=False,
        )
        assert isinstance(resp, LLMResponse)
        assert resp.content == ""
        assert resp.tool_calls == []

    def test_adapter_parse_response_missing_candidates_key(self):
        resp = GoogleAdapter._parse_response({}, tools_was_provided=False)
        assert isinstance(resp, LLMResponse)
        assert resp.content == ""

    def test_gateway_google_generate_content_blocked_returns_none(self, monkeypatch):
        gw = LLMGateway(provider="google", api_key="test-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        fake_session = _FakeSession(
            {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
        )
        monkeypatch.setattr(gw, "_get_session", lambda: fake_session)

        result = asyncio.run(
            gw._google_generate_content("gemini-2.5-flash", "hello", "sys")
        )
        assert result is None


# ---------------------------------------------------------------------------
# F3: providers legally return choices: [] -> IndexError at [0]
# ---------------------------------------------------------------------------


def _empty_choices_response():
    return SimpleNamespace(choices=[])


def _stub_openai_client(response):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=response))
        )
    )


class TestOpenAIEmptyChoices:
    def test_openai_compat_chat_empty_choices(self, monkeypatch):
        adapter = OpenAICompatAdapter(provider="openrouter", api_key="test-key")
        monkeypatch.setattr(
            adapter, "_get_client", lambda: _stub_openai_client(_empty_choices_response())
        )
        resp = asyncio.run(
            adapter.chat(model="test-model", messages=[{"role": "user", "content": "hi"}])
        )
        assert isinstance(resp, LLMResponse)
        assert resp.content == ""

    def test_gateway_legacy_path_empty_choices_returns_none(self, monkeypatch):
        # Force the legacy path: no registered adapter covers openrouter here.
        monkeypatch.setattr(
            "aja.orchestration.providers.get_adapter_class", lambda provider: None
        )
        gw = LLMGateway(provider="openrouter", api_key="test-key")
        monkeypatch.setattr(
            gw,
            "_get_openai_client",
            lambda: _stub_openai_client(_empty_choices_response()),
        )

        result = asyncio.run(gw.chat(model="gpt-4o", prompt="hello", retries=1))
        assert result is None


# ---------------------------------------------------------------------------
# F6: multimodal / tool-role history must not be dumped verbatim into
# Gemini {"text": <list>} parts
# ---------------------------------------------------------------------------


MULTIMODAL_CONTENT = [
    {"type": "text", "text": "describe this product"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
]


class TestGoogleMultimodalFlattening:
    def test_flatten_list_content_joins_text_parts(self):
        text = _flatten_google_content(MULTIMODAL_CONTENT)
        assert text == "describe this product"
        assert isinstance(text, str)

    def test_flatten_string_passthrough_and_none(self):
        assert _flatten_google_content("plain") == "plain"
        assert _flatten_google_content(None) == ""

    def test_flatten_multipart_text_joined(self):
        content = [
            {"type": "text", "text": "part one"},
            {"type": "image_url", "image_url": {"url": "http://x"}},
            {"type": "text", "text": "part two"},
        ]
        assert _flatten_google_content(content) == "part one\npart two"

    def test_adapter_convert_messages_multimodal_and_tool_role(self):
        messages = [
            {"role": "user", "content": MULTIMODAL_CONTENT},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "f"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result data"},
        ]
        contents = GoogleAdapter._convert_messages(messages)

        # User multimodal turn flattened to ONE string part, image dropped
        user_turn = contents[0]
        assert user_turn["role"] == "user"
        assert user_turn["parts"] == [{"text": "describe this product"}]

        # Tool result mapped to a Gemini-compatible user turn (not lost verbatim)
        tool_turn = contents[2]
        assert tool_turn["role"] == "user"
        assert len(tool_turn["parts"]) == 1
        assert isinstance(tool_turn["parts"][0]["text"], str)
        assert "result data" in tool_turn["parts"][0]["text"]
        assert "call_1" in tool_turn["parts"][0]["text"]

    def test_gateway_google_payload_flattens_history(self, monkeypatch):
        gw = LLMGateway(provider="google", api_key="test-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        fake_session = _FakeSession(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}]}},
                ]
            }
        )
        monkeypatch.setattr(gw, "_get_session", lambda: fake_session)

        prompt = [
            {"role": "user", "content": MULTIMODAL_CONTENT},
            {"role": "tool", "tool_call_id": "call_9", "content": "42"},
        ]
        result = asyncio.run(
            gw._google_generate_content("gemini-2.5-flash", prompt, "sys")
        )
        assert result == "ok"

        sent_contents = fake_session.last_payload["contents"]
        # No list ever reaches a Gemini text part
        for c in sent_contents:
            for part in c["parts"]:
                assert isinstance(part["text"], str)
        assert sent_contents[0]["parts"][0]["text"] == "describe this product"
        assert sent_contents[1]["role"] == "user"
        assert "42" in sent_contents[1]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


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


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
