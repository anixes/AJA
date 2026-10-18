"""Hermetic tests for the AJA serve entrypoint (runtime/serve.py + serve_cmd.py).

All three long-running component coroutines are patched with in-process fakes;
nothing touches LanceDB, the network, or real signal delivery.
"""

import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

import aja.runtime.serve as serve_mod


@pytest.fixture()
def fake_components(monkeypatch):
    started = {"gateway": False, "autonomy": False, "scheduler": False}

    async def fake_run_gateway():
        started["gateway"] = True
        await asyncio.sleep(3600)

    async def fake_main_loop(stop_event=None):
        started["autonomy"] = True
        assert stop_event is not None
        while not (stop_event is not None and stop_event.is_set()):
            await asyncio.sleep(0.01)
        return "stopped"

    class FakeScheduler:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            started["scheduler"] = True

        async def stop_async(self):
            return None

    monkeypatch.setattr(serve_mod, "run_gateway", fake_run_gateway)
    monkeypatch.setattr(serve_mod, "main_loop", fake_main_loop)
    monkeypatch.setattr(serve_mod, "CronScheduler", FakeScheduler)

    import aja.assistant as assistant_pkg

    monkeypatch.setattr(
        assistant_pkg,
        "register_briefing_jobs",
        lambda scheduler, **kw: [{"job": "briefing"}],
        raising=False,
    )
    return SimpleNamespace(started=started)


def test_registry_import_smoke():
    from aja.cli.commands.serve_cmd import cmd_serve  # noqa: F401

    from aja.cli.registry import registry

    assert callable(registry._handlers.get("serve"))


@pytest.mark.anyio
async def test_serve_starts_all_three_components(fake_components):
    stop_event = asyncio.Event()

    async def stop_later():
        await asyncio.sleep(0.05)
        stop_event.set()

    stopper = asyncio.create_task(stop_later())
    try:
        await asyncio.wait_for(serve_mod.serve(stop_event), timeout=5.0)
    finally:
        stopper.cancel()

    assert fake_components.started == {
        "gateway": True,
        "autonomy": True,
        "scheduler": True,
    }


@pytest.mark.anyio
async def test_serve_propagates_stop_and_exits_cleanly(fake_components):
    stop_event = asyncio.Event()
    start = time.monotonic()

    async def set_stop():
        await asyncio.sleep(0.02)
        stop_event.set()

    setter = asyncio.create_task(set_stop())
    serve_task = asyncio.create_task(serve_mod.serve(stop_event))
    done, _pending = await asyncio.wait({setter}, timeout=2.0)
    assert done, "stop setter did not finish"
    # Cancellation propagation: exits well within timeout after stop event set.
    await asyncio.wait_for(serve_task, timeout=5.0)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0
    assert serve_task.cancelled() is False


@pytest.mark.anyio
async def test_serve_tears_down_when_child_crashes(fake_components, monkeypatch):
    """An unexpected child failure must not hang serve() forever."""
    async def crashing_gateway():
        raise RuntimeError("gateway exploded")

    monkeypatch.setattr(serve_mod, "run_gateway", crashing_gateway)
    stop_event = asyncio.Event()
    serve_task = asyncio.create_task(serve_mod.serve(stop_event))
    await asyncio.wait_for(serve_task, timeout=5.0)


def test_main_loop_stop_event_exits_immediately(monkeypatch, capsys):
    """main_loop(stop_event=preset) returns without touching heavy deps."""
    import aja.runtime.autonomous_loop as al

    class FakeStore:
        def publish_heartbeat(self, *a, **kw):
            return None

    monkeypatch.setattr(al, "LanceRuntimeStore", FakeStore)

    fake_intent = SimpleNamespace(start=lambda: None, stop=lambda: None)
    fake_goal = SimpleNamespace(get_active_goals=lambda: [], run_step=None)

    monkeypatch.setitem(sys.modules, "aja.autonomy.intent_engine", SimpleNamespace(intent_engine=fake_intent))
    monkeypatch.setitem(sys.modules, "aja.goals.goal_engine", SimpleNamespace(goal_engine=fake_goal))

    async def noop_step():
        return None

    fake_goal.run_step = noop_step

    stop_event = asyncio.Event()
    stop_event.set()
    result = asyncio.run(al.main_loop(stop_event))
    assert result is None
