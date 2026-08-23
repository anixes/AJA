"""Unit tests for ConversationCore (aja.core.conversation) — fully mocked.

No LanceDB, no real gateway, no scheduler side effects. The subprocess
isolation test additionally proves a full mocked turn imports none of the
heavy AJA machinery (lancedb / aja.config / aja.api.bridge).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aja.core.conversation import (
    ConversationCore,
    InMemorySessionStore,
    IntentResult,
    SessionStore,
)
from aja.core.events import Delta, Error, Final, ToolFinished, ToolStarted
from aja.messaging.envelope import InboundMessage

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_LIB = REPO_ROOT / "libs" / "aja-core"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeGateway:
    """Records chat() kwargs; pops queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class FakeRegistry:
    def get_schemas(self, interactive=True):
        return [{"name": "echo_tool"}]


class FakeExecutor:
    def __init__(self, results=None):
        self.results = results or []
        self.dispatched = []

    async def dispatch_tool_calls(self, tool_calls=None, **kw):
        self.dispatched.extend(tool_calls or [])
        if self.results:
            return list(self.results)
        return [
            SimpleNamespace(tool=tc["tool"], success=True, data="ok")
            for tc in (tool_calls or [])
        ]


class RecordingTaskStore:
    def __init__(self):
        self.tasks = []

    def create_task(self, task):
        self.tasks.append(task)
        return dict(task, task_id="T-1")


class FakeSessionStore(InMemorySessionStore):
    def __init__(self):
        super().__init__()
        self.saved = {}

    async def save(self, chat_id, state):
        await super().save(chat_id, state)
        self.saved[chat_id] = state


def msg(text, chat_id="c1", user_id="u1", surface="telegram"):
    return InboundMessage(surface=surface, chat_id=chat_id, user_id=user_id, text=text)


async def drain(core, message):
    return [ev async for ev in core.handle(message)]


def make_core(**overrides):
    defaults = dict(
        gateway=FakeGateway(["Here is your answer."]),
        tools_registry=FakeRegistry(),
        executor=FakeExecutor(),
        sessions=FakeSessionStore(),
        recall_enabled=False,
    )
    defaults.update(overrides)
    return ConversationCore(**defaults)


# --------------------------------------------------------------------------- #
# CHAT pipeline
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_chat_yields_delta_then_final():
    core = make_core()
    events = await drain(core, msg("what is the capital of France?"))
    kinds = [type(e) for e in events]
    assert Final in kinds
    assert kinds[-1] is Final
    assert any(isinstance(e, Delta) for e in events)
    final = events[-1]
    assert final.text == "Here is your answer."
    assert not any(isinstance(e, Error) for e in events)


@pytest.mark.anyio
async def test_chat_tool_round_trip_emits_started_finished():
    gateway = FakeGateway(
        [
            {"content": "", "tool_calls": [{"name": "search_web", "arguments": '{"q": "python"}'}]},
            "All done.",
        ]
    )
    executor = FakeExecutor(results=[SimpleNamespace(tool="search_web", success=True, data="res")])
    core = make_core(gateway=gateway, executor=executor)
    events = await drain(core, msg("search for python"))

    started = [e for e in events if isinstance(e, ToolStarted)]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert len(started) == 1 and started[0].name == "search_web"
    assert len(finished) == 1 and finished[0].success is True
    assert events[-1].text == "All done."


@pytest.mark.anyio
async def test_chat_failure_yields_error_event():
    class BoomGateway:
        async def chat(self, **kw):
            raise RuntimeError("provider down")

    core = make_core(gateway=BoomGateway())
    events = await drain(core, msg("hello"))
    errors = [e for e in events if isinstance(e, Error)]
    assert errors and errors[0].code in {"PIPELINE_FAILED", "EXECUTE_FAILED"}
    assert not any(isinstance(e, Final) for e in events)


# --------------------------------------------------------------------------- #
# Deterministic intents
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_reminder_intent_fires_creator_and_final():
    created = {}

    def fake_creator(task, when_raw="", chat_id=None, **kw):
        created.update(task=task, when_raw=when_raw, chat_id=chat_id)
        return {"run_at": "2026-08-25T15:00:00"}

    core = make_core(reminder_creator=fake_creator)
    events = await drain(core, msg("remind me to call mom tomorrow 3pm"))
    final = events[-1]
    assert isinstance(final, Final) and final.text.startswith("⏰ Saved")
    assert "call mom" in created["task"]
    assert created["when_raw"]
    assert created["chat_id"] == "c1"


@pytest.mark.anyio
async def test_reminder_unparseable_time_yields_error():
    core = make_core(reminder_creator=lambda *a, **k: None)
    events = await drain(core, msg("remind me to call mom sometime"))
    assert any(isinstance(e, Error) and e.code == "REMINDER_UNPARSED" for e in events)


@pytest.mark.anyio
async def test_task_capture_creates_task():
    store = RecordingTaskStore()
    core = make_core(task_store=store)
    events = await drain(core, msg("remember to water the plants"))
    final = events[-1]
    assert isinstance(final, Final) and final.text.startswith("📌 Saved")
    assert store.tasks and store.tasks[0]["title"].startswith("water")
    assert store.tasks[0]["status"] == "pending"


@pytest.mark.anyio
async def test_reminders_list_formats_jobs():
    def lister():
        return [{"job_id": "J1", "goal": "Reminder: call mom", "schedule_expr": "one-shot"}]

    core = make_core(reminders_lister=lister)
    events = await drain(core, msg("/reminders"))
    final = events[-1]
    assert isinstance(final, Final)
    assert "call mom" in final.text and "J1" in final.text


@pytest.mark.anyio
async def test_status_uses_status_provider():
    core = make_core(status_provider=lambda: {"worker": "ONLINE", "missions_active": 2})
    events = await drain(core, msg("status"))
    final = events[-1]
    assert isinstance(final, Final)
    assert "ONLINE" in final.text and "missions_active: 2" in final.text


@pytest.mark.anyio
async def test_mission_intent_creates_mission_record():
    class MissionStore:
        def create_mission(self, goal):
            return {"mission_id": "M-ABC123"}

    core = make_core(mission_store=MissionStore(), recall_enabled=False)
    events = await drain(core, msg("/mission run the test suite now"))
    final = events[-1]
    assert isinstance(final, Final)
    assert "M-ABC123" in final.text


# --------------------------------------------------------------------------- #
# Grounding / recall
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_recall_injection_appears_in_llm_prompt():
    async def recall_fn(query):
        return [
            {
                "role": "user",
                "content": "we discussed kafka consumer offset reset strategy",
                "timestamp": "2026-08-23",
                "score": 0.91,
            }
        ]

    gateway = FakeGateway(["got it"])
    core = make_core(gateway=gateway, recall_fn=recall_fn, recall_enabled=True)
    await drain(core, msg("continue that kafka discussion"))
    call = gateway.calls[0]
    # Recall is injected via the system role, never polluting chat history.
    rendered = str(call.get("system") or "") + "\n".join(
        str(m.get("content")) for m in call["prompt"]
    )
    assert "kafka consumer offset reset strategy" in rendered
    assert all(
        "kafka consumer offset reset strategy" not in str(m.get("content"))
        for m in call["prompt"]
    )


@pytest.mark.anyio
async def test_no_recall_block_when_disabled():
    gateway = FakeGateway(["ok"])
    calls = []

    async def recall_fn(query):
        calls.append(query)
        return [{"role": "user", "content": "stale"}]

    core = make_core(gateway=gateway, recall_fn=recall_fn, recall_enabled=False)
    await drain(core, msg("hi again"))
    assert calls == []
    first = gateway.calls[0]["prompt"][0]
    assert first["role"] != "system"


# --------------------------------------------------------------------------- #
# Auth + persistence
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_auth_denial_yields_single_error_event():
    core = make_core(authorizer=lambda surface, uid, cid: False)
    events = await drain(core, msg("secret question"))
    assert len(events) == 1
    err = events[0]
    assert isinstance(err, Error)
    assert err.code == "AUTH_DENIED"
    assert err.recoverable is False


@pytest.mark.anyio
async def test_history_persists_across_handles_via_session_store():
    store = FakeSessionStore()
    core = make_core(
        gateway=FakeGateway(["first answer", "second answer"]),
        sessions=store,
        recall_enabled=False,
    )
    await drain(core, msg("turn one"))
    assert "c1" in store.saved

    await drain(core, msg("turn two"))
    saved_history = store.saved["c1"]["history"]
    roles_contents = [(m["role"], m["content"]) for m in saved_history]
    assert ("user", "turn one") in roles_contents
    assert ("assistant", "first answer") in roles_contents
    assert ("user", "turn two") in roles_contents
    assert ("assistant", "second answer") in roles_contents


@pytest.mark.anyio
async def test_default_session_store_is_in_memory_protocol():
    store = InMemorySessionStore()
    assert isinstance(store, SessionStore)
    state = await store.load("x")
    state["history"].append({"role": "user", "content": "a"})
    again = await store.load("x")
    assert again["history"] == [{"role": "user", "content": "a"}]


# --------------------------------------------------------------------------- #
# Subprocess isolation
# --------------------------------------------------------------------------- #

_ISOLATION_SCRIPT = r"""
import asyncio
import sys

sys.path.insert(0, r"{core_lib}")

from aja.core.conversation import ConversationCore, InMemorySessionStore
from aja.messaging.envelope import InboundMessage


class GW:
    def __init__(self):
        self.calls = []
    async def chat(self, **kw):
        self.calls.append(kw)
        return "isolated reply"


class Reg:
    def get_schemas(self, interactive=True):
        return []


class Ex:
    async def dispatch_tool_calls(self, tool_calls=None, **kw):
        return []


class Store:
    def __init__(self):
        self.state = {{"history": []}}
    async def load(self, chat_id):
        return self.state
    async def save(self, chat_id, state):
        self.state = state


async def main():
    core = ConversationCore(
        gateway=GW(), tools_registry=Reg(), executor=Ex(),
        sessions=Store(), recall_enabled=False,
    )
    inbound = InboundMessage(surface="cli", chat_id="iso", user_id="u", text="ping")
    events = [ev async for ev in core.handle(inbound)]
    from aja.core.events import Final
    assert any(isinstance(e, Final) and e.text == "isolated reply" for e in events), events


asyncio.run(main())

forbidden = [m for m in ("lancedb", "aja.config", "aja.api.bridge") if m in sys.modules]
assert not forbidden, f"forbidden modules imported: {{forbidden}}"
print("ISOLATION_OK")
"""


def test_subprocess_isolation_fresh_interpreter_imports_only_core(tmp_path):
    script_path = tmp_path / "isolation_probe.py"
    script_path.write_text(_ISOLATION_SCRIPT.format(core_lib=str(CORE_LIB)), encoding="utf-8")

    proc = subprocess_run([sys.executable, str(script_path)])
    assert proc.returncode == 0, proc.stderr
    assert "ISOLATION_OK" in proc.stdout


def subprocess_run(cmd):
    import subprocess

    env_base = {k: v for k, v in __import__("os").environ.items() if k != "PYTEST_CURRENT_TEST"}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env_base)


# --------------------------------------------------------------------------- #
# Intent classification unit checks
# --------------------------------------------------------------------------- #


def test_classify_deterministic_fast_paths():
    core = make_core()
    cases = {
        "/reminders": "REMINDERS_LIST",
        "/status": "STATUS",
        "/mission deploy the service": "MISSION",
        "remind me to stretch at 5pm": "REMINDER",
        "remember to buy milk": "TASK_CAPTURE",
        "add task review PR": "TASK_CAPTURE",
        "hey how are you?": "CHAT",
    }
    for text, expected in cases.items():
        result = core._stage_classify({"history": []}, text)
        assert isinstance(result, IntentResult)
        assert result.type == expected, f"{text!r} -> {result.type}, want {expected}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

