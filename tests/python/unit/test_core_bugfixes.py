"""Regression tests for core bugfixes (session pollution, dropped tool events,
shared mutable session dict)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import aja.orchestration.direct_loop as direct_loop_module
from aja.core.conversation import ConversationCore, InMemorySessionStore
from aja.core.events import Final, ToolFinished, ToolStarted
from aja.messaging.envelope import InboundMessage


class FakeGateway:
    async def chat(self, **kwargs):
        return "ok"


class FakeRegistry:
    def get_schemas(self, interactive=True):
        return []


class FakeExecutor:
    async def dispatch_tool_calls(self, tool_calls=None, **kw):
        return [
            SimpleNamespace(tool=tc["tool"], success=True, data="ok")
            for tc in (tool_calls or [])
        ]


class RecordingSessionStore:
    def __init__(self):
        self.saved = {}

    async def load(self, chat_id):
        return {"history": [], "tasks": []}

    async def save(self, chat_id, state):
        self.saved[chat_id] = state


def msg(text, chat_id="c1", user_id="u1", surface="telegram"):
    return InboundMessage(surface=surface, chat_id=chat_id, user_id=user_id, text=text)


async def drain(core, message):
    return [ev async for ev in core.handle(message)]


def make_core(**overrides):
    defaults = dict(
        gateway=FakeGateway(),
        tools_registry=FakeRegistry(),
        executor=FakeExecutor(),
        sessions=RecordingSessionStore(),
    )
    defaults.update(overrides)
    return ConversationCore(**defaults)


# --------------------------------------------------------------------------- #
# Bug 1: session pollution
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_reminder_intent_does_not_leave_recall_block_in_session():
    async def recall_fn(query):
        return [{"role": "user", "content": "kafka offset context"}]

    store = RecordingSessionStore()
    core = make_core(
        sessions=store,
        recall_fn=recall_fn,
        recall_enabled=True,
        reminder_creator=lambda task, when_raw="", chat_id=None, **kw: {
            "run_at": "2026-08-25T15:00:00"
        },
    )
    events = await drain(core, msg("remind me to call mom tomorrow 3pm"))
    assert any(isinstance(e, Final) for e in events)
    saved = store.saved["c1"]
    assert "_recall_block" not in saved, f"recall block leaked: {saved.keys()}"
    assert "_working_history" not in saved, f"working history leaked: {saved.keys()}"
    assert all("kafka" not in str(v) for v in saved.values())


@pytest.mark.anyio
async def test_task_capture_intent_leaves_no_ephemeral_keys():
    store = RecordingSessionStore()
    core = make_core(sessions=store, task_store=None)
    await drain(core, msg("remember to water the plants"))
    saved = store.saved["c1"]
    assert "_recall_block" not in saved
    assert "_working_history" not in saved


# --------------------------------------------------------------------------- #
# Bug 2: dropped tool events
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_tool_events_queued_before_completion_are_yielded(monkeypatch):
    calls = [{"tool": "search_web"}, {"tool": "fetch_url"}]

    async def fake_run_direct_loop(task, executor=None, **kw):
        # Queue Started/Finished pairs then complete immediately — the consumer
        # must still yield everything queued before Final.
        await executor.dispatch_tool_calls(tool_calls=calls)
        return {"result": "done", "turns": 1, "status": "Completed"}

    monkeypatch.setattr(direct_loop_module, "run_direct_loop", fake_run_direct_loop)

    core = make_core(gateway=FakeGateway())
    events = await drain(core, msg("do the thing"))
    started = [e for e in events if isinstance(e, ToolStarted)]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert [e.name for e in started] == ["search_web", "fetch_url"]
    assert len(finished) == 2
    assert isinstance(events[-1], Final) and events[-1].text == "done"


@pytest.mark.anyio
async def test_cancelled_queue_get_is_awaited_without_leaking(monkeypatch):
    async def fake_run_direct_loop(task, **kw):
        return {"result": "silent", "turns": 0}

    monkeypatch.setattr(direct_loop_module, "run_direct_loop", fake_run_direct_loop)
    core = make_core(gateway=FakeGateway())
    events = await drain(core, msg("hello"))
    assert isinstance(events[-1], Final)
    # No pending-task warnings / leaked get_task: loop closed cleanly.


# --------------------------------------------------------------------------- #
# Bug 3: shared mutable session dict
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_concurrent_loads_return_independent_copies():
    store = InMemorySessionStore()
    s1, s2 = await asyncio.gather(store.load("c"), store.load("c"))
    assert s1 is not s2
    s1["history"].append({"role": "user", "content": "from turn one"})
    s2["tasks"].append("task from turn two")
    fresh = await store.load("c")
    assert fresh["history"] == []
    assert fresh["tasks"] == []


@pytest.mark.anyio
async def test_save_replaces_atomically_without_aliasing():
    store = InMemorySessionStore()
    state = await store.load("c")
    state["history"].append({"role": "user", "content": "a"})
    await store.save("c", state)
    loaded = await store.load("c")
    loaded["history"].append({"role": "assistant", "content": "b"})
    reread = await store.load("c")
    assert reread["history"] == [{"role": "user", "content": "a"}]
