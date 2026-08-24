"""Night-shift Wave 1 / E5 regression tests.

Covers the async/event-loop hardening fixes:
- E5#1: goal_engine._step_planning no longer blocks the loop on the sync
  planner (expand_goal runs via asyncio.to_thread).
- E5#2: CronScheduler.tick_loop offloads LanceDB store reads/writes and
  event-sink emits off the shared event loop.
- E5#3: LanceRuntimeEventSink.emit_async offload + shared runtime sink;
  IntentEngine.loop executes intents off-loop.
- E5#4: serve._install_signal_handlers Windows fallback routes stop_event.set
  through loop.call_soon_threadsafe.
"""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

import aja.scheduler.telegram as telegram_mod
from aja.runtime.events import (
    LanceRuntimeEventSink,
    get_shared_runtime_sink,
)
from aja.scheduler.cron_scheduler import CronScheduler


MAIN_THREAD_ID = threading.main_thread().ident


class FakeSink:
    def __init__(self):
        self.events = []

    def emit(self, payload):
        self.events.append(payload)
        return "sink-evt"


async def _count_beats(seconds: float) -> int:
    """Counts event-loop heartbeats for ``seconds``, proving responsiveness."""
    beats = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(0.02)
        beats += 1
    return beats


# --------------------------------------------------------------------- E5#1


@pytest.mark.anyio
async def test_step_planning_runs_expand_goal_off_loop(monkeypatch):
    """expand_goal (sync planner w/ LLM round-trip) must not block the loop."""
    from aja.goals.goal_engine import Goal, GoalEngine
    from aja.planning.planner import _fallback_graph

    engine = GoalEngine.__new__(GoalEngine)
    engine.memory = SimpleNamespace(record_scheduler_event=lambda **kw: None)
    engine.save_state = lambda: None

    seen_threads = []

    def slow_expand(goal):
        seen_threads.append(threading.get_ident())
        time.sleep(0.25)  # simulates the blocking LLM planning call
        return _fallback_graph(goal.objective)

    monkeypatch.setattr(engine, "expand_goal", slow_expand)
    goal = Goal("write a sonnet about databases", priority=1)

    async def heartbeat():
        while True:
            await asyncio.sleep(0.02)
            counter["beats"] += 1

    counter = {"beats": 0}
    hb = asyncio.create_task(heartbeat())
    try:
        await engine._step_planning(goal)
    finally:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass
    beats = counter["beats"]

    assert seen_threads and seen_threads[0] != MAIN_THREAD_ID
    # The loop kept ticking (>=3 heartbeats) while the 250ms blocking plan ran.
    assert beats >= 3
    assert goal.status == "PLANNING"
    assert goal.plan is not None


@pytest.mark.anyio
async def test_step_planning_marks_failed_on_planner_error(monkeypatch):
    from aja.goals.goal_engine import Goal, GoalEngine

    engine = GoalEngine.__new__(GoalEngine)
    engine.memory = SimpleNamespace(record_scheduler_event=lambda **kw: None)
    engine.save_state = lambda: None

    def boom(goal):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(engine, "expand_goal", boom)
    goal = Goal("explode", priority=1)

    await engine._step_planning(goal)
    assert goal.status == "FAILED"


# --------------------------------------------------------------------- E5#2


class SlowLanceStore:
    """Task-store double with deliberately blocking (disk-like) IO."""

    def __init__(self):
        self.list_threads = []
        self.update_threads = []

    def list_tasks(self, status=None, statuses=None, limit=50):
        self.list_threads.append(threading.get_ident())
        time.sleep(0.15)
        return [
            {
                "task_id": "job-1",
                "context": "never due",
                "owner": "scheduler",
                "status": "scheduled",
                # Unparseable expr -> neither duration nor cron match.
                "metadata_json": json.dumps(
                    {"schedule_expr": "not-a-real-schedule"}
                ),
            }
        ]

    def update_task(self, task_id, updates):
        self.update_threads.append(threading.get_ident())
        return {"task_id": task_id}


@pytest.mark.anyio
async def test_tick_loop_store_io_runs_off_loop():
    store = SlowLanceStore()
    sched = CronScheduler(
        check_interval=0.05, store=store, event_sink=FakeSink()
    )
    sched._running = True

    beats_task = asyncio.create_task(_count_beats(0.6))
    tick = asyncio.create_task(sched.tick_loop())
    beats = await beats_task
    sched._running = False
    tick.cancel()
    try:
        await tick
    except asyncio.CancelledError:
        pass

    assert store.list_threads, "tick loop never listed tasks"
    assert all(t != MAIN_THREAD_ID for t in store.list_threads)
    # ~0.6s window with 150ms blocking reads: loop must have stayed live.
    assert beats >= 10


@pytest.mark.anyio
async def test_tick_loop_emits_events_off_loop():
    sink = FakeSink()
    emit_threads = []
    orig_emit = sink.emit

    def spying_emit(payload):
        emit_threads.append(threading.get_ident())
        return orig_emit(payload)

    sink.emit = spying_emit

    class DueStore(SlowLanceStore):
        def list_tasks(self, status=None, statuses=None, limit=50):
            return [
                {
                    "task_id": "job-due",
                    "context": "overlapping job",
                    "owner": "scheduler",
                    "status": "scheduled",
                    "metadata_json": json.dumps({"schedule_expr": "* * * * *"}),
                }
            ]

    sched = CronScheduler(check_interval=0.05, store=DueStore(), event_sink=sink)
    sched._running = True
    # Force the overlap branch: job already marked running -> SKIPPED_OVERLAP
    # emission without spawning real execution.
    sched._running_jobs.add("job-due")

    tick = asyncio.create_task(sched.tick_loop())
    for _ in range(200):
        await asyncio.sleep(0.02)
        if emit_threads:
            break
    sched._running = False
    tick.cancel()
    try:
        await tick
    except asyncio.CancelledError:
        pass

    assert emit_threads, "SKIPPED_OVERLAP event was never emitted"
    assert all(t != MAIN_THREAD_ID for t in emit_threads)


# --------------------------------------------------------------------- E5#3


@pytest.mark.anyio
async def test_lance_sink_emit_async_returns_and_offloads():
    seen_threads = []

    class FakeMemory:
        def add_runtime_event(self, event):
            seen_threads.append(threading.get_ident())
            time.sleep(0.05)
            return "evt-1"

    sink = LanceRuntimeEventSink(memory=FakeMemory())
    result = await sink.emit_async({"event_type": "TEST_EVENT", "message": "hi"})

    assert result == "evt-1"
    assert seen_threads[0] != MAIN_THREAD_ID


def test_shared_runtime_sink_is_singleton():
    assert get_shared_runtime_sink() is get_shared_runtime_sink()


@pytest.mark.anyio
async def test_intent_engine_loop_executes_intents_off_loop(monkeypatch):
    import aja.autonomy.intent_engine as ie_mod
    from aja.autonomy.intent_engine import IntentEngine

    engine = IntentEngine()

    class FakeGoalEngine:
        goals = []

        def add_goal(self, objective, priority=1):
            return "goal-1"

    class FakeExperienceStore:
        def save(self, *a, **kw):
            return None

    monkeypatch.setattr(ie_mod, "goal_engine", FakeGoalEngine())
    monkeypatch.setattr(ie_mod, "experience_store", FakeExperienceStore())
    reports = []
    monkeypatch.setattr(
        telegram_mod,
        "_send_telegram_report",
        lambda msg: reports.append(msg),
    )
    engine.save_cooldowns = lambda: None

    execute_threads = []

    def fake_execute(intent):
        execute_threads.append(threading.get_ident())

    engine.autonomy_enabled = True
    engine._running = True
    engine.intent_last_run = {}
    monkeypatch.setattr(engine, "execute", fake_execute)

    loop_task = asyncio.create_task(engine.loop())
    try:
        for _ in range(250):
            await asyncio.sleep(0.02)
            if execute_threads:
                break
    finally:
        engine._running = False
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    assert execute_threads, "intent loop never executed an intent"
    assert execute_threads[0] != MAIN_THREAD_ID


# --------------------------------------------------------------------- E5#4


@pytest.mark.anyio
async def test_serve_windows_fallback_routes_stop_via_call_soon_threadsafe(
    monkeypatch,
):
    from aja.runtime import serve as serve_mod
    import signal as stdlib_signal

    loop = asyncio.get_running_loop()

    def fake_getsignal(sig):
        return stdlib_signal.SIG_DFL

    installed = {}

    def fake_signal(sig, handler):
        installed[sig] = handler
        return None

    monkeypatch.setattr(serve_mod.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(serve_mod.signal, "signal", fake_signal)

    # Force the Windows-style fallback even on POSIX test hosts.
    def raise_not_implemented(sig, callback, *args):
        raise NotImplementedError()

    monkeypatch.setattr(loop, "add_signal_handler", raise_not_implemented)

    cst_calls = []
    orig_cst = loop.call_soon_threadsafe

    def spying_call_soon_threadsafe(cb, *args):
        cst_calls.append(cb)
        return orig_cst(cb, *args)

    monkeypatch.setattr(loop, "call_soon_threadsafe", spying_call_soon_threadsafe)

    stop_event = asyncio.Event()
    cleanup = serve_mod._install_signal_handlers(stop_event)
    try:
        assert stdlib_signal.SIGTERM in installed
        assert stdlib_signal.SIGINT in installed
        installed[stdlib_signal.SIGTERM]()
        await asyncio.sleep(0)
        assert stop_event.is_set()
        # The set must be routed through call_soon_threadsafe.
        assert len(cst_calls) == 1
    finally:
        cleanup()


@pytest.mark.anyio
async def test_serve_posix_add_signal_handler_preferred(monkeypatch):
    """When add_signal_handler is supported, no signal.signal fallback installs."""
    from aja.runtime import serve as serve_mod
    import signal as stdlib_signal

    loop = asyncio.get_running_loop()
    registered = []

    def fake_add(sig, cb, *args):
        registered.append((sig, cb))

    monkeypatch.setattr(loop, "add_signal_handler", fake_add)
    installed_via_signal = []
    monkeypatch.setattr(
        serve_mod.signal,
        "signal",
        lambda sig, handler: installed_via_signal.append(sig),
    )

    stop_event = asyncio.Event()
    cleanup = serve_mod._install_signal_handlers(stop_event)

    assert {(sig, cb) for sig, cb in registered} and not installed_via_signal
    for sig, cb in registered:
        cb()
    await asyncio.sleep(0)
    assert stop_event.is_set()

