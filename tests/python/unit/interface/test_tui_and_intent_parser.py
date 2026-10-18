"""Night-Shift Wave 1 / E4 regression tests.

Covers (mock-heavy, no real TUI launches):
- T4#1: AJAShell awaits the async LLMGateway.chat (no coroutine TypeError).
- A3#1: audit_and_execute runs subprocess off-loop with a 60s timeout.
- T4#2: parse_intent / parse_intent_async reject non-dict LLM JSON.
- T4#3: content=None history + Delta(text=None) guards in conversation,
  renderers, and dashboard.
- A3#2: dashboard _safe_provider runs sync providers off the event loop.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List

import pytest
from unittest.mock import AsyncMock, MagicMock

from aja.core.conversation import ConversationCore, InMemorySessionStore
from aja.core.events import Delta, Error, Final
from aja.interface.renderers import EventRenderer
from aja.interface.tui import AJAShell
from aja.messaging.envelope import InboundMessage
from aja.tui.dashboard import AJADashboard


# --------------------------------------------------------------------------- #
# Shared fakes
# --------------------------------------------------------------------------- #


class FakeGateway:
    def __init__(self, responses):
        self.responses = list(responses)

    async def chat(self, **kwargs):
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class FakeRegistry:
    def get_schemas(self, interactive=True):
        return [{"name": "echo_tool"}]


class FakeExecutor:
    async def dispatch_tool_calls(self, tool_calls=None, **kw):
        return [
            SimpleNamespace(tool=tc["tool"], success=True, data="ok")
            for tc in (tool_calls or [])
        ]


def tmsg(text, chat_id="c1", user_id="u1", surface="telegram"):
    return InboundMessage(surface=surface, chat_id=chat_id, user_id=user_id, text=text)


# --------------------------------------------------------------------------- #
# T4#1 — AJAShell gateway.chat must be awaited
# --------------------------------------------------------------------------- #


def _make_shell(gateway):
    app = AJAShell("google", "dummy", "test-model")
    app.gateway = gateway
    app.query_one = MagicMock(return_value=MagicMock())
    logs: List[str] = []
    app.log_aja = logs.append
    return app, logs


@pytest.mark.anyio
async def test_tui_awaits_async_gateway_chat(monkeypatch):
    gateway = SimpleNamespace(chat=AsyncMock(return_value="Just a plain reply."))
    app, logs = _make_shell(gateway)
    event = SimpleNamespace(value="what is the meaning of life?")

    await asyncio.wait_for(app.on_input_submitted(event), timeout=5.0)

    gateway.chat.assert_awaited_once()
    assert any("plain reply" in m for m in logs)


@pytest.mark.anyio
async def test_tui_non_str_chat_response_does_not_crash(monkeypatch):
    """Legacy path can return None on failure — membership tests must not see it."""
    gateway = SimpleNamespace(chat=AsyncMock(return_value=None))
    app, logs = _make_shell(gateway)
    event = SimpleNamespace(value="what is the meaning of life?")

    await asyncio.wait_for(app.on_input_submitted(event), timeout=5.0)
    assert not any("Traceback" in m for m in logs)


@pytest.mark.anyio
async def test_tui_cmd_response_triggers_audit_execute():
    """A <cmd>...</cmd> reply routes into (now awaited) audit_and_execute."""
    executed: List[str] = []

    async def fake_audit(cmd):
        executed.append(cmd)

    gateway = SimpleNamespace(
        chat=AsyncMock(return_value='Here you go: <cmd>echo hi</cmd>')
    )
    app, _ = _make_shell(gateway)
    app.audit_and_execute = fake_audit
    monkeypatch_shutil_none = None  # shutil.which("echo") exists on most systems

    await asyncio.wait_for(
        app.on_input_submitted(SimpleNamespace(value="run echo hi")), timeout=5.0
    )
    assert executed == ["echo hi"]


# --------------------------------------------------------------------------- #
# A3#1 — audit_and_execute: off-loop + timeout
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_audit_execute_times_out_instead_of_freezing(monkeypatch):
    monkeypatch.setattr(
        "aja.interface.tui.classify_command",
        lambda cmd: {"decision": "allow", "root": "safe", "level": "info", "reasons": []},
    )

    loop_thread = threading.get_ident()
    worker_threads: List[int] = []

    def slow_run(*a, **k):
        worker_threads.append(threading.get_ident())
        time.sleep(2.0)
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr("aja.interface.tui.subprocess.run", slow_run)

    app = AJAShell("google", "dummy", "test-model")
    app.query_one = MagicMock(return_value=MagicMock())
    app.log_aja = lambda m: None
    app.SHELL_TIMEOUT_S = 0.2

    start = time.monotonic()
    await asyncio.wait_for(app.audit_and_execute("ping -t localhost"), timeout=5.0)
    elapsed = time.monotonic() - start

    assert worker_threads and worker_threads[0] != loop_thread
    assert elapsed < 1.5  # did not wait for the full 2s sleep


@pytest.mark.anyio
async def test_audit_execute_success_logs_output(monkeypatch):
    monkeypatch.setattr(
        "aja.interface.tui.classify_command",
        lambda cmd: {"decision": "allow", "root": "safe", "level": "info", "reasons": []},
    )
    monkeypatch.setattr(
        "aja.interface.tui.subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout="hello out", stderr=""),
    )
    monkeypatch.setattr(
        "aja.interface.tui.get_system_state",
        lambda: {
            "active_tasks": 0,
            "pending_tasks": 0,
            "is_healthy": True,
            "load_level": "low",
        },
    )
    monkeypatch.setattr("platform.system", lambda: "Linux")

    app = AJAShell("google", "dummy", "test-model")
    app.query_one = MagicMock(return_value=MagicMock())
    app.action_refresh_state = lambda: None
    logs: List[str] = []
    app.log_aja = logs.append

    await asyncio.wait_for(app.audit_and_execute("echo hello"), timeout=5.0)
    assert not any("timed out" in m for m in logs)  # completed normally


@pytest.mark.anyio
async def test_audit_execute_deny_short_circuits(monkeypatch):
    calls: List[str] = []
    monkeypatch.setattr(
        "aja.interface.tui.classify_command",
        lambda cmd: {
            "decision": "deny",
            "root": "catastrophic",
            "level": "danger",
            "reasons": ["root deletion"],
        },
    )
    monkeypatch.setattr(
        "aja.interface.tui.subprocess.run", lambda *a, **k: calls.append("ran")
    )
    app = AJAShell("google", "dummy", "test-model")
    app.query_one = MagicMock(return_value=MagicMock())
    logs: List[str] = []
    app.log_aja = logs.append

    await app.audit_and_execute("rm -rf /")
    assert calls == []
    assert any("blocked" in m for m in logs)


# --------------------------------------------------------------------------- #
# T4#2 — intent parser rejects non-dict LLM JSON
# --------------------------------------------------------------------------- #

_FALLBACK_MARKERS = ("question", 0.0)


def _is_question_fallback(intent: Dict[str, Any]) -> bool:
    return (
        isinstance(intent, dict)
        and intent.get("type") == "question"
        and intent.get("confidence") == 0.0
        and "rephrase" in str(intent.get("response", ""))
    )


def _install_llm(monkeypatch, raw: str) -> None:
    import aja.llm as llm_mod

    monkeypatch.setattr(llm_mod, "completion", lambda **kw: raw, raising=False)

    async def fake_async(**kw):
        return raw

    monkeypatch.setattr(llm_mod, "completion_async", fake_async, raising=False)


@pytest.mark.parametrize("raw", ['[1, 2, 3]', '"just a string"', '42', 'null'])
@pytest.mark.anyio
async def test_parse_intent_non_dict_json_falls_back(monkeypatch, raw):
    from aja.interface.intent_parser import parse_intent

    _install_llm(monkeypatch, raw)
    result = parse_intent("explain the system architecture", [])
    assert _is_question_fallback(result)


@pytest.mark.anyio
async def test_parse_intent_valid_dict_passes_through(monkeypatch):
    from aja.interface.intent_parser import parse_intent

    payload = {
        "type": "goal",
        "goal": "refactor locks",
        "command": None,
        "tool_calls": None,
        "response": "On it.",
        "confidence": 0.9,
    }
    _install_llm(monkeypatch, json.dumps(payload))
    result = parse_intent("explain the system architecture", [])
    assert result == payload


@pytest.mark.parametrize("raw", ['["array"]', '"scalar"'])
@pytest.mark.anyio
async def test_parse_intent_async_non_dict_json_falls_back(monkeypatch, raw):
    from aja.interface.intent_parser import parse_intent_async

    _install_llm(monkeypatch, raw)
    result = await parse_intent_async("explain the system architecture", [])
    assert _is_question_fallback(result)


@pytest.mark.anyio
async def test_parse_intent_async_valid_dict_passes_through(monkeypatch):
    from aja.interface.intent_parser import parse_intent_async

    payload = {"type": "control", "command": "doctor", "response": "ok", "confidence": 1.0}
    _install_llm(monkeypatch, "```json\n" + json.dumps(payload) + "\n```")
    result = await parse_intent_async("explain the system architecture", [])
    assert result == payload


# --------------------------------------------------------------------------- #
# T4#3 — content=None guards
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_chat_history_content_none_yields_empty_final_not_none(monkeypatch):
    """T4#3 source guard: assistant history entry with content=None must not
    produce Final(text=None)."""
    import aja.orchestration.direct_loop as dl

    async def fake_loop(*a, **kw):
        return {}  # no structured result → falls back to history scan

    monkeypatch.setattr(dl, "run_direct_loop", fake_loop)

    store = InMemorySessionStore()
    await store.save("c1", {"history": [{"role": "assistant", "content": None}]})
    core = ConversationCore(
        gateway=FakeGateway(["unused"]),
        tools_registry=FakeRegistry(),
        executor=FakeExecutor(),
        sessions=store,
        recall_enabled=False,
    )
    events = [ev async for ev in core.handle(tmsg("what is the capital of France?"))]

    assert not any(isinstance(e, Error) for e in events)
    final = events[-1]
    assert isinstance(final, Final)
    assert final.text == ""


@pytest.mark.anyio
async def test_renderer_stream_events_delta_none_guard():
    class RecordingRenderer(EventRenderer):
        def __init__(self):
            super().__init__()
            self.seen: List[str] = []

        def render_delta(self, text: str) -> None:
            self.seen.append(text)

    r = RecordingRenderer()

    async def gen() -> AsyncIterator[Any]:
        yield Delta(text=None)
        yield Final(text="done")

    final = await r.stream_events(gen())
    assert r.seen == [""]  # None coerced to "", no TypeError
    assert final == "done"


@pytest.mark.anyio
async def test_dashboard_render_event_delta_none_guard():
    app = AJADashboard(core=SimpleNamespace(handle=None))
    app.query_one = MagicMock(return_value=MagicMock())
    app._set_status = lambda detail: None

    await app.render_event(Delta(text=None))
    assert app._delta_buffer == [""]  # None never enters the join buffer


# --------------------------------------------------------------------------- #
# A3#2 — dashboard sidebar providers run off the Textual loop
# --------------------------------------------------------------------------- #


class MockCore:
    async def handle(self, msg: InboundMessage) -> AsyncIterator[Any]:
        yield Final(text="ok")


def _make_dashboard(providers: Dict[str, Any]) -> AJADashboard:
    defaults: Dict[str, Any] = {
        "health_check": lambda: [{"name": "python", "ok": True, "detail": "3.12"}],
        "model_info": "test",
    }
    defaults.update(providers)
    return AJADashboard(core=MockCore(), **defaults)


@pytest.mark.anyio
async def test_safe_provider_runs_sync_fn_in_worker_thread():
    main_thread = threading.get_ident()
    seen_threads: List[int] = []

    def sync_provider() -> List[Dict[str, Any]]:
        seen_threads.append(threading.get_ident())
        return [{"title": "task"}]

    app = _make_dashboard({"focus_refresh": sync_provider})
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        results = await app._safe_provider(app._focus_refresh)
        assert results == [{"title": "task"}]
    assert seen_threads and seen_threads[0] != main_thread


@pytest.mark.anyio
async def test_safe_provider_supports_async_fn_and_error_degradation():
    async def async_provider() -> List[Dict[str, Any]]:
        await asyncio.sleep(0)
        return [{"mission_id": "m1"}]

    def boom() -> List[Dict[str, Any]]:
        raise RuntimeError("lance down")

    app = _make_dashboard({"missions_refresh": boom})
    # _safe_provider touches no DOM — no need to mount the app.
    ok = await app._safe_provider(async_provider)
    err = await app._safe_provider(boom)
    assert ok == [{"mission_id": "m1"}]
    assert "_error" in err[0] and "RuntimeError" in err[0]["_error"]
