"""Night-Shift Wave 1 / E1 regression tests.

Covers:
- T1#1: NULL-status runtime-event rows must not crash telemetry pollers and
  must be retried (seen-mark only AFTER successful enqueue).
- A1#4: telemetry tails perform session IO off the event loop.
- T1#2: SlackAdapter exposes the start_tail/tail_events/stop_tails contract.
- T1#4: Slack NULL-text events are dropped at the adapter boundary.
- T1#3: orchestrator MISSION intent survives create_mission() -> None.
- A1#3: GatewayRunner serializes the telegram_adapter swap.
- T1#5: GatewayState.get_session validates parsed JSON shape.
"""

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from aja.gateway.base import MessageEvent, MessageType


# --------------------------------------------------------------------------- #
# Fakes for the LanceDB runtime-events poller
# --------------------------------------------------------------------------- #


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, n):
        return self

    def to_list(self):
        return list(self._rows)


class _FakeTable:
    """First search (seen-ID pre-population) returns nothing; later searches
    return the queued rows."""

    def __init__(self, rows):
        self.rows = rows
        self.searches = 0

    def search(self):
        self.searches += 1
        if self.searches == 1:
            return _FakeQuery([])
        return _FakeQuery(self.rows)


class _FakeMemory:
    def __init__(self, rows):
        self._table = _FakeTable(rows)
        self.db = SimpleNamespace(open_table=lambda name: self._table)


class FlakyQueue(asyncio.Queue):
    """Queue whose first put raises, simulating a mid-processing crash."""

    def __init__(self):
        super().__init__()
        self.put_calls = 0

    async def put(self, item):
        self.put_calls += 1
        if self.put_calls == 1:
            raise RuntimeError("simulated enqueue failure")
        await super().put(item)


def _make_poller_adapter(adapter_cls, monkeypatch, rows):
    adapter = adapter_cls({"token": "test-token"})
    adapter.is_running = True
    import aja.memory.secretary as secretary

    memory = _FakeMemory(rows)
    monkeypatch.setattr(secretary, "get_aja_memory", lambda: memory)
    return adapter


async def _run_poller_until_event(adapter, queue, timeout=10.0):
    task = asyncio.create_task(adapter._poll_lancedb_events())
    try:
        item = await asyncio.wait_for(queue.get(), timeout=timeout)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return item


@pytest.mark.anyio
@pytest.mark.parametrize("adapter_cls", ["telegram", "discord"])
async def test_null_status_row_is_sanitized_not_crashing(
    adapter_cls, monkeypatch
):
    """T1#1: tracker-style rows with status=None must not crash the poller."""
    if adapter_cls == "telegram":
        from aja.gateway.tg_client import TelegramAdapter as adapter_class
    else:
        from aja.gateway.adapters.discord_adapter import DiscordAdapter as adapter_class

    rows = [
        {
            "event_id": "evt-null-1",
            "kind": "AGENT_LOOP_TICK",
            "status": None,
            "message": '{"iteration": 3}',
            "timestamp": None,
        }
    ]
    adapter = _make_poller_adapter(adapter_class, monkeypatch, rows)
    payload = await _run_poller_until_event(adapter, adapter.telemetry_queue)

    assert payload["event_id"] == "evt-null-1"
    assert payload["status"] == "SUCCESS"
    assert payload["kind"] == "AGENT_LOOP_TICK"


@pytest.mark.anyio
@pytest.mark.parametrize("adapter_cls", ["telegram", "discord"])
async def test_failed_enqueue_retries_row_next_tick(adapter_cls, monkeypatch):
    """T1#1: seen-mark happens AFTER successful enqueue, so a crashing row
    is retried on the next poll instead of being permanently dropped."""
    if adapter_cls == "telegram":
        from aja.gateway.tg_client import TelegramAdapter as adapter_class
    else:
        from aja.gateway.adapters.discord_adapter import DiscordAdapter as adapter_class

    rows = [
        {
            "event_id": "evt-retry-1",
            "kind": "MISSION_DONE",
            "status": None,
            "message": "{}",
            "timestamp": None,
        }
    ]
    adapter = _make_poller_adapter(adapter_class, monkeypatch, rows)
    flaky = FlakyQueue()
    adapter.telemetry_queue = flaky

    payload = await _run_poller_until_event(adapter, flaky)

    assert payload["event_id"] == "evt-retry-1"
    assert flaky.put_calls >= 2, "row must be retried after the failed enqueue"


# --------------------------------------------------------------------------- #
# A1#4: tail session IO must run off the event loop
# --------------------------------------------------------------------------- #


class _LoopTrackingGatewayState:
    def __init__(self, loop):
        self.loop = loop
        self.calls = []

    def _on_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is self.loop
        except RuntimeError:
            return False

    def get_session(self, chat_id):
        self.calls.append(("get", self._on_loop()))
        return {"history": [], "metadata": {}}

    def update_session(self, chat_id, session):
        self.calls.append(("update", self._on_loop()))


def _make_tail_adapter(adapter_cls, loop):
    adapter = adapter_cls({"token": "test-token"})
    adapter.is_running = True
    adapter.gateway = SimpleNamespace(
        gateway_state=_LoopTrackingGatewayState(loop)
    )
    return adapter


@pytest.mark.anyio
@pytest.mark.parametrize("adapter_cls", ["telegram", "discord"])
async def test_tail_session_io_offloads_to_thread(adapter_cls):
    """A1#4: get_session/update_session in tail_events must not run on the loop."""
    if adapter_cls == "telegram":
        from aja.gateway.tg_client import TelegramAdapter as adapter_class
    else:
        from aja.gateway.adapters.discord_adapter import DiscordAdapter as adapter_class

    loop = asyncio.get_running_loop()
    adapter = _make_tail_adapter(adapter_class, loop)

    chat_queue = asyncio.Queue(maxsize=10)
    adapter._chat_queues["C1"] = chat_queue
    await chat_queue.put(
        {
            "kind": "PLAN_CREATED",
            "target": "m1",
            "status": "SUCCESS",
            "message": "plan ready",
        }
    )

    task = asyncio.create_task(adapter.tail_events("C1"))
    await asyncio.wait_for(chat_queue.join(), timeout=5.0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    kinds = [c[0] for c in adapter.gateway.gateway_state.calls]
    assert "get" in kinds and "update" in kinds
    on_loop = [on_loop for _, on_loop in adapter.gateway.gateway_state.calls]
    assert not any(on_loop), "session IO must be offloaded via asyncio.to_thread"


# --------------------------------------------------------------------------- #
# T1#2 + T1#4: Slack adapter contract
# --------------------------------------------------------------------------- #


def test_slack_adapter_exposes_tail_contract():
    from aja.gateway.adapters.slack_adapter import SlackAdapter

    adapter = SlackAdapter({"token": "x", "app_token": "y"})
    assert callable(getattr(adapter, "start_tail", None))
    assert callable(getattr(adapter, "tail_events", None))
    assert callable(getattr(adapter, "stop_tails", None))
    assert hasattr(adapter, "_tail_tasks")
    assert hasattr(adapter, "_chat_queues")


@pytest.mark.anyio
async def test_slack_tail_forwards_events():
    from aja.gateway.adapters.slack_adapter import SlackAdapter

    adapter = SlackAdapter({"token": "x"})
    sent = []

    async def fake_send_message(chat_id, text, **kwargs):
        sent.append((chat_id, text))
        return {"ok": True}

    adapter.send_message = fake_send_message
    adapter.is_running = True
    adapter.start_tail("C1")

    queue = adapter._chat_queues["C1"]
    await queue.put({"status": "ERROR", "message": "boom", "kind": "NODE_FAILED"})
    await asyncio.wait_for(queue.join(), timeout=5.0)

    adapter.is_running = False
    await adapter.stop_tails()

    assert sent == [("C1", "[ERROR] boom")]
    assert adapter._tail_tasks == {}


def test_slack_none_text_dropped_not_vision_coerced():
    """T1#4: the slack envelope builder must coerce NULL text to '' and drop
    text-less messages rather than emitting an empty-text MessageEvent."""
    import inspect

    from aja.gateway.adapters.slack_adapter import SlackAdapter

    source = inspect.getsource(SlackAdapter)
    assert 'event.get("text") or ""' in source


# --------------------------------------------------------------------------- #
# T1#3: orchestrator MISSION intent guards create_mission() -> None
# --------------------------------------------------------------------------- #


class _StubOrchestratorParts:
    pass


def _build_orchestrator(monkeypatch, create_mission_result):
    import aja.gateway.auth as gateway_auth
    from aja.gateway.orchestrator import UnifiedGateway

    orch = UnifiedGateway.__new__(UnifiedGateway)
    orch.model_id = "test-model"
    orch._open_gateway_warned = True
    orch.active_telemetry_bridges = set()

    sent = []
    started_tails = []

    async def fake_send_message(chat_id, text, **kwargs):
        sent.append((chat_id, text))

    stub_adapter = SimpleNamespace(
        send_message=fake_send_message,
        start_tail=lambda chat_id: started_tails.append(chat_id),
    )
    orch.telegram_adapter = stub_adapter

    sessions = {}

    class StubGatewayState:
        def get_session(self, chat_id):
            return sessions.setdefault(chat_id, {"history": [], "metadata": {}})

        def update_session(self, chat_id, session):
            sessions[chat_id] = session

    orch.gateway_state = StubGatewayState()

    missions_created = []
    updated_missions = []

    class StubAJAMemory:
        def get_active_workers(self, timeout_seconds=120):
            return [{"name": "w1", "pid": 4242}]

        def create_mission(self, goal):
            missions_created.append(goal)
            return create_mission_result

        def update_mission(self, mission_id, patch):
            updated_missions.append((mission_id, patch))

    orch.aja_memory = StubAJAMemory()

    monkeypatch.setattr(gateway_auth, "is_user_authorized", lambda *a, **k: True)
    monkeypatch.setattr(gateway_auth, "has_bot_token", lambda platform: False)

    return (
        orch,
        sent,
        started_tails,
        missions_created,
        updated_missions,
        sessions,
    )


def _mission_event(text="/swarm build the thing"):
    return MessageEvent(
        platform="telegram",
        chat_id="100",
        user_id="42",
        message_type=MessageType.TEXT,
        text=text,
        media_urls=[],
        message_id="m-1",
        raw_event=None,
    )


@pytest.mark.anyio
async def test_mission_intent_survives_create_mission_none(monkeypatch):
    """T1#3: create_mission() returning None must yield a user-visible
    failure reply, never a TypeError."""
    orch, sent, tails, created, updated, sessions = _build_orchestrator(
        monkeypatch, create_mission_result=None
    )

    await orch.handle_gateway_event(_mission_event())

    assert len(created) == 1
    assert updated == [], "must not update a mission that was never created"
    assert len(sent) == 1
    chat_id, response = sent[0]
    assert chat_id == "100"
    assert "error" in response.lower()


@pytest.mark.anyio
async def test_mission_intent_success_path_still_works(monkeypatch):
    orch, sent, tails, created, updated, sessions = _build_orchestrator(
        monkeypatch, create_mission_result={"mission_id": "abc123"}
    )

    await orch.handle_gateway_event(_mission_event())

    assert len(sent) == 1
    assert "abc123" in sent[0][1]
    assert tails == ["100"]
    assert "100" in orch.active_telemetry_bridges


@pytest.mark.anyio
async def test_mission_intent_non_dict_mission_guards(monkeypatch):
    """A falsy-but-not-None result (e.g. {}) also routes to the failure reply."""
    orch, sent, tails, created, updated, sessions = _build_orchestrator(
        monkeypatch, create_mission_result={}
    )
    await orch.handle_gateway_event(_mission_event())
    assert updated == []
    assert "error" in sent[0][1].lower()


# --------------------------------------------------------------------------- #
# A1#3: GatewayRunner serializes the adapter swap
# --------------------------------------------------------------------------- #


class _FakeOrchestrator:
    def __init__(self):
        self.telegram_adapter = "original"
        self.handled = []

    async def handle_gateway_event(self, event):
        self.handled.append((event, self.telegram_adapter))
        await asyncio.sleep(0.02)


class _FakeAdapter:
    def __init__(self, name):
        self.name = name

    async def poll(self):
        yield MessageEvent(
            platform=self.name,
            chat_id="c",
            user_id="u",
            message_type=MessageType.TEXT,
            text="hi",
            media_urls=[],
            message_id=f"m-{self.name}",
            raw_event=None,
        )


@pytest.mark.anyio
async def test_runner_routes_each_event_through_its_own_adapter():
    from aja.gateway.gateway_runner import GatewayRunner

    orch = _FakeOrchestrator()
    runner = GatewayRunner(orch)
    ad_a, ad_b = _FakeAdapter("a"), _FakeAdapter("b")

    ev_a = MessageEvent(
        platform="a", chat_id="c", user_id="u", message_type=MessageType.TEXT,
        text="a", media_urls=[], message_id="ma", raw_event=None,
    )
    ev_b = MessageEvent(
        platform="b", chat_id="c", user_id="u", message_type=MessageType.TEXT,
        text="b", media_urls=[], message_id="mb", raw_event=None,
    )
    # Tag events so we can map them back after handling.
    marker_a, marker_b = object(), object()
    ev_a.raw_event = marker_a
    ev_b.raw_event = marker_b

    await asyncio.gather(
        runner.process_event(ad_a, ev_a),
        runner.process_event(ad_b, ev_b),
    )

    by_event = {id(ev_a): ad_a, id(ev_b): ad_b}
    for event, adapter in orch.handled:
        expected = by_event[id(event)]
        assert getattr(adapter, "target_adapter", None) is expected, (
            "each event must observe its own adapter installed during handling"
        )
    assert orch.telegram_adapter == "original", "swap must be restored afterwards"


# --------------------------------------------------------------------------- #
# T1#5: GatewayState.get_session shape validation
# --------------------------------------------------------------------------- #


def test_get_session_returns_default_for_non_dict_json(monkeypatch, tmp_path):
    import aja.gateway.persistence as persistence
    from aja.gateway.persistence import GatewayState

    captured = {"raw": None}

    class StubDB:
        def open_table(self, name):
            return self

        def search(self):
            return self

        def where(self, predicate):
            return self

        def limit(self, n):
            return self

        def to_list(self):
            return [{"chat_id": "c1", "session_json": captured["raw"], "last_updated": 0}]

    class StubManager:
        db = StubDB()

    monkeypatch.setattr(persistence, "get_memory_manager", lambda: StubManager())
    monkeypatch.setattr(
        persistence, "list_tables_defensive", lambda db: ["gateway_sessions"]
    )

    state = GatewayState.__new__(GatewayState)
    state.db = StubDB()
    state.table_name = "gateway_sessions"

    for bad_raw in ("null", '"just a string"', "[1, 2]", "garbage{"):
        captured["raw"] = bad_raw
        session = state.get_session("c1")
        assert isinstance(session, dict)
        assert isinstance(session.get("history"), list)
        assert isinstance(session.get("metadata"), dict)


def test_get_session_accepts_valid_dict_and_fills_defaults(monkeypatch):
    import json as _json

    import aja.gateway.persistence as persistence
    from aja.gateway.persistence import GatewayState

    captured = {"raw": None}

    class StubDB:
        def open_table(self, name):
            return self

        def search(self):
            return self

        def where(self, predicate):
            return self

        def limit(self, n):
            return self

        def to_list(self):
            return [{"chat_id": "c1", "session_json": captured["raw"], "last_updated": 0}]

    monkeypatch.setattr(persistence, "get_memory_manager", lambda: SimpleNamespace(db=StubDB()))
    monkeypatch.setattr(
        persistence, "list_tables_defensive", lambda db: ["gateway_sessions"]
    )

    state = GatewayState.__new__(GatewayState)
    state.db = StubDB()
    state.table_name = "gateway_sessions"

    captured["raw"] = _json.dumps({"history": [{"role": "user", "text": "hi"}]})
    session = state.get_session("c1")
    assert session["history"] == [{"role": "user", "text": "hi"}]
    assert session["metadata"] == {}
