"""Night-shift Wave 2 / E5 regression tests.

Covers:
- F1: explicit timeouts (AJA_LLM_TIMEOUT_S) on AsyncOpenAI clients and the
  gateway aiohttp session; SDK default 600s/300s no longer apply.
- F2: SDK internal retries disabled (max_retries=0) — AJA owns retry policy.
- F3: jittered backoff with deterministic bounds.
- F4: status-carrying ValueError on the Copilot Responses path is classified
  as a non-retryable 4xx (single attempt, no backoff sleeps).
- F7: gateway.chat returns None (not "") on failure/empty adapter content;
  llm.completion_async passes None through instead of coercing to "".
- F5/F6: self_healer + decision/engine await chat() properly and guard
  falsy results (no file destruction / no parse-error masking).
"""

import asyncio

import pytest

import aja.decision.engine as engine_mod
import aja.llm as llm_mod
import aja.orchestration.providers as providers_pkg
from aja.orchestration import gateway as gw_mod
from aja.orchestration.gateway import LLMGateway
from aja.orchestration.providers import openai_compat as oc_mod
from aja.orchestration.providers.base import LLMResponse
from aja.utils import self_healer


# ---------------------------------------------------------------- helpers


def _total_timeout(timeout_obj):
    return float(getattr(timeout_obj, "total", timeout_obj))


class _StubNamespace:

    def __init__(self, exc=None, calls=None):
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                calls.append(kwargs)
                if exc is not None:
                    raise exc

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _force_legacy_path(monkeypatch):
    monkeypatch.setattr(providers_pkg, "get_adapter_class", lambda p: None)


# ------------------------------------------------------------ F1/F2: timeouts


def test_env_knob_default_is_120s(monkeypatch):
    monkeypatch.delenv("AJA_LLM_TIMEOUT_S", raising=False)
    assert gw_mod._llm_timeout_s() == 120.0
    assert oc_mod._llm_timeout_s() == 120.0
    monkeypatch.setenv("AJA_LLM_TIMEOUT_S", "not-a-number")
    assert gw_mod._llm_timeout_s() == 120.0
    monkeypatch.setenv("AJA_LLM_TIMEOUT_S", "42.5")
    assert gw_mod._llm_timeout_s() == 42.5


def test_gateway_openai_client_timeout_and_no_sdk_retries(monkeypatch):
    monkeypatch.setenv("AJA_LLM_TIMEOUT_S", "77")

    async def main():
        gw = LLMGateway(provider="nvidia", api_key="test-key")
        client = gw._get_openai_client()
        try:
            assert _total_timeout(client.timeout) == pytest.approx(77.0)
            assert client.max_retries == 0
        finally:
            await gw.close()

    asyncio.run(main())


def test_adapter_openai_client_timeout_and_no_sdk_retries(monkeypatch):
    monkeypatch.setenv("AJA_LLM_TIMEOUT_S", "55")

    async def main():
        adapter = oc_mod.OpenAICompatAdapter(provider="nvidia", api_key="test-key")
        client = adapter._get_client()
        try:
            assert _total_timeout(client.timeout) == pytest.approx(55.0)
            assert client.max_retries == 0
        finally:
            await adapter.close()

    asyncio.run(main())


def test_gateway_aiohttp_session_uses_env_timeout(monkeypatch):
    monkeypatch.setenv("AJA_LLM_TIMEOUT_S", "88")

    async def main():
        gw = LLMGateway(provider="nvidia", api_key="test-key")
        session = gw._get_session()
        assert session.timeout.total == pytest.approx(88.0)
        await gw.close()

    asyncio.run(main())


# ------------------------------------------------------------- F3: jitter


@pytest.mark.parametrize("mod", [gw_mod, oc_mod])
def test_backoff_jitter_bounds(monkeypatch, mod):
    cap = min(30.0, 2.0 ** 10)
    for attempt in range(0, 12):
        expected_cap = min(30.0, 2.0 ** attempt)
        monkeypatch.setattr(mod.random, "random", lambda: 0.5)
        mid = mod._backoff_sleep_seconds(attempt)
        assert expected_cap * 0.75 == pytest.approx(mid)
        monkeypatch.setattr(mod.random, "random", lambda: 0.0)
        assert mod._backoff_sleep_seconds(attempt) == pytest.approx(expected_cap * 0.5)
        monkeypatch.setattr(mod.random, "random", lambda: 1.0)
        assert mod._backoff_sleep_seconds(attempt) == pytest.approx(expected_cap)
    assert cap == 30.0  # capped at attempt>=5


# --------------------------------------- F4: status-carrying Responses error


class _FakeResp:
    def __init__(self, status, body="detail"):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def json(self):
        return {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, status, posts):
        self._status = status
        self._posts = posts

    def post(self, url, json=None, headers=None):
        self._posts.append(url)
        return _FakeResp(self._status)


def test_copilot_responses_400_fast_fails_with_status(monkeypatch):
    _force_legacy_path(monkeypatch)

    async def fake_backoff(attempt):  # would stall the test if invoked
        raise AssertionError("backoff sleep must not fire on a 4xx")

    monkeypatch.setattr(gw_mod, "_backoff_sleep", fake_backoff)
    posts = []

    import aja.copilot_auth as copilot_auth

    monkeypatch.setattr(copilot_auth, "resolve_copilot_token",
                        lambda: ("raw-token", "test"))
    monkeypatch.setattr(copilot_auth, "get_copilot_api_token",
                        lambda raw: "api-token")
    monkeypatch.setattr(copilot_auth, "invalidate_copilot_cache", lambda: None)
    monkeypatch.setattr(copilot_auth, "copilot_request_headers",
                        lambda is_agent_turn=True: {})

    async def main():
        gw = LLMGateway(provider="copilot", api_key="dummy")
        monkeypatch.setattr(gw, "_get_session", lambda: _FakeSession(400, posts))
        result = await gw.chat(model="gpt-5", prompt="hi")
        await gw.close()
        return result

    result = asyncio.run(main())
    assert result is None
    assert len(posts) == 1  # deterministic 400 must NOT burn retries


# ------------------------------- legacy-loop classifier behavior (F4 support)


def test_status_carrying_error_fast_fails_in_legacy_loop(monkeypatch):
    monkeypatch.delenv("AJA_LLM_TIMEOUT_S", raising=False)
    _force_legacy_path(monkeypatch)

    async def fake_backoff(attempt):
        raise AssertionError("backoff must not fire on a fast-fail status")

    monkeypatch.setattr(gw_mod, "_backoff_sleep", fake_backoff)

    err = ValueError("deterministic 400")
    err.status_code = 400
    calls = []

    async def main():
        gw = LLMGateway(provider="nvidia", api_key="k")
        monkeypatch.setattr(gw, "_get_openai_client",
                            lambda: _StubNamespace(exc=err, calls=calls))
        try:
            return await gw.chat(model="m", prompt="hi")
        finally:
            await gw.close()

    result = asyncio.run(main())
    assert result is None
    assert len(calls) == 1


def test_statusless_error_still_retries_with_jittered_backoff(monkeypatch):
    monkeypatch.delenv("AJA_LLM_TIMEOUT_S", raising=False)
    _force_legacy_path(monkeypatch)

    recorded = []

    async def fake_backoff(attempt):
        recorded.append(attempt)

    monkeypatch.setattr(gw_mod, "_backoff_sleep", fake_backoff)

    err = ValueError("Copilot Responses API Error 400: no status attached")
    calls = []

    async def main():
        gw = LLMGateway(provider="nvidia", api_key="k")
        monkeypatch.setattr(gw, "_get_openai_client",
                            lambda: _StubNamespace(exc=err, calls=calls))
        try:
            return await gw.chat(model="m", prompt="hi")
        finally:
            await gw.close()

    result = asyncio.run(main())
    assert result is None
    assert len(calls) == 3  # default retries=3 all burned
    assert recorded == [1, 2]  # slept between attempts, not after the last


# --------------------------------------------- F7: failure sentinel contract


def test_gateway_chat_none_on_empty_adapter_content(monkeypatch):
    monkeypatch.delenv("AJA_LLM_TIMEOUT_S", raising=False)

    class FakeAdapter:
        def __init__(self, api_key="", base_url=""):
            pass

        async def chat(self, **kwargs):
            return LLMResponse(content="", model="m")

        async def close(self):
            pass

    monkeypatch.setattr(providers_pkg, "get_adapter_class", lambda p: FakeAdapter)
    gw = LLMGateway(provider="nvidia", api_key="k")
    result = asyncio.run(gw.chat(model="m", prompt="hi"))
    assert result is None


def test_gateway_chat_str_on_success_adapter_content(monkeypatch):
    monkeypatch.delenv("AJA_LLM_TIMEOUT_S", raising=False)

    class FakeAdapter:
        def __init__(self, api_key="", base_url=""):
            pass

        async def chat(self, **kwargs):
            return LLMResponse(content="hello world", model="m")

        async def close(self):
            pass

    monkeypatch.setattr(providers_pkg, "get_adapter_class", lambda p: FakeAdapter)
    gw = LLMGateway(provider="nvidia", api_key="k")
    result = asyncio.run(gw.chat(model="m", prompt="hi"))
    assert result == "hello world"


def test_completion_async_passes_none_through(monkeypatch):
    class FakeGW:
        provider = "nvidia"

        async def chat(self, **kwargs):
            return None

    monkeypatch.setattr(llm_mod, "get_gateway_for_model",
                        lambda m: (FakeGW(), "m"))
    assert asyncio.run(llm_mod.completion_async("hi")) is None


def test_completion_async_passes_str_through(monkeypatch):
    class FakeGW:
        provider = "nvidia"

        async def chat(self, **kwargs):
            return "answer"

    monkeypatch.setattr(llm_mod, "get_gateway_for_model",
                        lambda m: (FakeGW(), "m"))
    assert asyncio.run(llm_mod.completion_async("hi")) == "answer"


# --------------------------------------------------- F5: self_healer awaits


class _HealerGW:
    content = "return finalPrice;"

    def __init__(self, *args, **kwargs):
        pass

    async def chat(self, *args, **kwargs):
        return type(self).content


def _setup_healer(tmp_path, monkeypatch, gw_cls, checks):
    target = tmp_path / "broken.ts"
    target.write_text("return finaPrice;", encoding="utf-8")
    monkeypatch.setattr(self_healer, "run_health_check", lambda p: checks.pop(0))
    monkeypatch.setattr(self_healer, "KEY", "real-key")
    monkeypatch.setattr(self_healer, "PROVIDER", "nvidia")
    monkeypatch.setattr(self_healer, "MODEL", "llama-3")
    monkeypatch.setattr(self_healer, "LLMGateway", gw_cls)
    return target


def test_self_healer_waits_and_writes_fix(tmp_path, monkeypatch):
    checks = [(False, "err"), (True, None)]
    target = _setup_healer(tmp_path, monkeypatch, _HealerGW, checks)
    self_healer.heal_system(str(target))
    assert target.read_text(encoding="utf-8") == "return finalPrice;"


def test_self_healer_never_truncates_file_on_failure(tmp_path, monkeypatch):
    class NoneGW(_HealerGW):
        content = None

    checks = [(False, "err"), (True, None)]
    target = _setup_healer(tmp_path, monkeypatch, NoneGW, checks)
    self_healer.heal_system(str(target))
    # File must be untouched — an outage must not destroy production code.
    assert target.read_text(encoding="utf-8") == "return finaPrice;"


def test_self_healer_result_is_not_a_coroutine(tmp_path, monkeypatch):
    class RecordingGW(_HealerGW):
        async def chat(self, *args, **kwargs):
            return "fixed code"

    checks = [(False, "err"), (True, None)]
    target = _setup_healer(tmp_path, monkeypatch, RecordingGW, checks)
    self_healer.heal_system(str(target))
    assert target.read_text(encoding="utf-8") == "fixed code"


# ---------------------------------------------- F6: decision/engine awaits


class _EngineGW:
    def __init__(self, response):
        self._response = response
        self.api_key = "k"
        self.provider = "nvidia"

    async def chat(self, **kwargs):
        return self._response


def _make_engine(monkeypatch, response):
    monkeypatch.setattr(engine_mod, "get_gateway",
                        lambda: _EngineGW(response))
    return engine_mod.DecisionEngine()


def test_engine_decide_falls_back_visibly_on_none_response(monkeypatch):
    eng = _make_engine(monkeypatch, None)
    decision = eng.decide("do a thing", {})
    assert decision["type"] == "NEW"
    assert decision["confidence"] == 0.0
    assert "gateway" in decision["reason"].lower()


def test_engine_decide_parses_valid_llm_response(monkeypatch):
    eng = _make_engine(
        monkeypatch,
        '{"type": "SKILL", "confidence": 0.9, "reason": "exact match"}',
    )
    decision = eng.decide("do a thing", {})
    assert decision["type"] == "SKILL"
    assert decision["confidence"] >= 0.9


def test_engine_chat_response_is_awaited_not_coroutine(monkeypatch):
    seen = {}

    class ProbeGW(_EngineGW):
        async def chat(self, **kwargs):
            seen["called"] = True
            return '{"type": "ASK", "confidence": 0.8, "reason": "ambiguous"}'

    monkeypatch.setattr(engine_mod, "get_gateway", lambda: ProbeGW(None))
    eng = engine_mod.DecisionEngine()
    decision = eng.decide("unclear thing", {})
    assert seen.get("called") is True
    assert decision["type"] == "ASK"
