"""TDD tests for the FUTURE async planning API.

These tests target ``Planner.decompose_async(goal, current_state=None) -> PlanGraph``,
which does not exist yet. Until the refactor agent implements it, these tests
fail with AttributeError on ``decompose_async`` -- that is expected and
acceptable. Everything else (sync regression guard, gateway mocking style,
anyio marks) follows the existing suite conventions:

- Gateway/LLM mocking pattern: tests/python/planning/test_generator_diversity.py
- anyio marks + loop-heartbeat assertions: tests/python/unit/test_nightshift_wave1_e5.py

Contract under test:
1. decompose_async returns a valid PlanGraph for a simple goal.
2. decompose_async NEVER calls aja.llm.run_async_synchronously (no nested
   loop-blocking bridge on the async path).
3. The event loop stays responsive while decompose_async runs (concurrent
   heartbeat task keeps ticking during ~200ms fake LLM calls).
4. Sync decompose() still returns a PlanGraph unchanged (regression guard).
5. goal_engine._step_planning awaits rather than thread-spawns (no net new
   threads created while planning).
"""
import asyncio
import json
import os
import threading
import time

import pytest

# Embedding model backends (fastembed/sentence-transformers) are not installed
# in this environment; force the deterministic hashing mock so method retrieval
# never attempts a real model load.
os.environ.setdefault("AJA_MOCK_EMBEDDINGS", "1")

from aja.planning.models import PlanGraph

VALID_PLAN_RAW = json.dumps(
    {
        "goal": "Write a haiku about databases",
        "nodes": [
            {
                "id": "P1",
                "task": "Draft haiku",
                "type": "primitive",
                "dod": {
                    "success_criteria": "done",
                    "validation_type": "deterministic",
                },
            }
        ],
    }
)


class FakeAsyncGateway:
    """Async gateway double: complete/chat are awaitables returning valid JSON."""

    def __init__(self, delay: float = 0.0, raw: str = VALID_PLAN_RAW):
        self.delay = delay
        self.raw = raw
        self.complete_calls = []
        self.chat_calls = []

    async def complete(self, system=None, user=None, model=None,
                       retries=3, temperature=None, **kwargs):
        self.complete_calls.append({"system": system, "user": user})
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.raw

    async def chat(self, prompt=None, system=None, model=None,
                   retries=3, temperature=None, **kwargs):
        self.chat_calls.append({"system": system, "prompt": prompt})
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.raw


def _fake_completion_async(raw_plan):
    """Build an async completion_async double that answers each caller with
    context-appropriate JSON (verifier / simulator / planner prompts differ)."""

    async def fake(prompt=None, system_prompt="", model=None, **kwargs):
        sys_text = (system_prompt or "").lower()
        if "verifier" in sys_text or "verification" in sys_text:
            return json.dumps({"valid": True, "conflicts": [], "missing_preconditions": []})
        if "simulator" in sys_text:
            return json.dumps({
                "success_probability": 0.9,
                "risk": 0.1,
                "latency": 0.2,
                "complexity": 0.2,
                "predicted_failures": [],
                "feedback": "ok",
            })
        return raw_plan

    return fake


def _install_completion_async(monkeypatch, gw):
    """Patch aja.llm.completion_async so verifier/simulation/revision paths hit
    the fake gateway instead of resolving REAL gateways. All planning modules
    import completion_async inside their functions, so this single patch point
    All planning modules do ``from aja.llm import completion_async`` inside
    their functions, so the aja.llm patch point is the one that actually
    binds; we additionally stamp each consumer module's namespace (raising=False,
    since the name is imported lazily) so any future module-level import is
    covered too."""
    import aja.llm as llm_mod

    calls = {"count": 0}

    async def fake_completion_async(*args, **kwargs):
        calls["count"] += 1
        return await _fake_completion_async(VALID_PLAN_RAW)(*args, **kwargs)

    monkeypatch.setattr(llm_mod, "completion_async", fake_completion_async,
                        raising=False)
    # Defense-in-depth: cover module-level imports at each usage site.
    import aja.planning.verifier as verifier_mod
    import aja.planning.generator as generator_mod
    import aja.planning.simulation as simulation_mod
    monkeypatch.setattr(verifier_mod, "completion_async", fake_completion_async,
                        raising=False)
    monkeypatch.setattr(generator_mod, "completion_async",
                        fake_completion_async, raising=False)
    monkeypatch.setattr(simulation_mod, "completion_async",
                        fake_completion_async, raising=False)
    return calls


def _patch_generator_planner(monkeypatch, gw):
    """generator.generate_candidate_plans_async builds a fresh throwaway
    Planner() without a gateway; force that planner to reuse our fake gateway."""
    import aja.planning.planner as planner_mod

    real_planner_cls = planner_mod.Planner

    def _patched(*args, **kwargs):
        kwargs.pop("gateway", None)
        return real_planner_cls(gateway=gw)

    # generator.py does ``from aja.planning.planner import Planner`` inside the
    # async function body, so patching planner.Planner is what binds; also
    # stamp generator's namespace for defense-in-depth.
    monkeypatch.setattr(planner_mod, "Planner", _patched)
    import aja.planning.generator as generator_mod
    monkeypatch.setattr(generator_mod, "Planner", _patched, raising=False)


def _forbid_real_gateways(monkeypatch):
    """Make any attempt to resolve a REAL gateway explode loudly instead of
    silently opening live httpx2/openai connections (whose cleanup then
    crashes when the test event loop closes)."""
    import aja.llm as llm_mod

    def _boom(*args, **kwargs):
        raise AssertionError("real gateway resolution is forbidden in tests")

    monkeypatch.setattr(llm_mod, "get_gateway", _boom, raising=False)


def _forbid_run_async_synchronously(monkeypatch):
    """Make every plausible import site of run_async_synchronously explode."""

    def _boom(*args, **kwargs):
        raise AssertionError(
            "run_async_synchronously must never be called on the async path"
        )

    try:
        import aja.llm as llm_mod
        monkeypatch.setattr(llm_mod, "run_async_synchronously", _boom,
                            raising=False)
    except ImportError:
        pass
    try:
        import aja.planning.planner as planner_mod
        monkeypatch.setattr(planner_mod, "run_async_synchronously", _boom,
                            raising=False)
    except ImportError:
        pass


# --------------------------------------------------------------------- 1 & 2


@pytest.mark.anyio
async def test_decompose_async_returns_plan_graph(monkeypatch):
    from aja.planning.planner import Planner

    _forbid_run_async_synchronously(monkeypatch)
    gw = FakeAsyncGateway()
    _patch_generator_planner(monkeypatch, gw)
    completion_calls = _install_completion_async(monkeypatch, gw)
    planner = Planner(gateway=gw)

    graph = await planner.decompose_async("Write a haiku about databases")

    assert isinstance(graph, PlanGraph)
    assert graph.goal == "Write a haiku about databases"
    assert graph.nodes, "expected at least one planned node"
    # The fake gateway must actually be exercised (no silent bypass onto real
    # gateways via the throwaway Planner or module-level completion_async).
    assert gw.complete_calls, "fake gateway.complete was never called"
    assert completion_calls["count"] > 0, (
        "module-level completion_async was never called"
    )


@pytest.mark.anyio
async def test_decompose_async_never_bridges_via_run_async_synchronously(
    monkeypatch,
):
    from aja.planning.planner import Planner

    _forbid_run_async_synchronously(monkeypatch)
    gw = FakeAsyncGateway()
    _patch_generator_planner(monkeypatch, gw)
    _install_completion_async(monkeypatch, gw)
    planner = Planner(gateway=gw)

    # Must succeed even though the sync bridge raises AssertionError if used.
    graph = await planner.decompose_async("Write a haiku about databases")
    assert isinstance(graph, PlanGraph)
    assert gw.complete_calls, "fake gateway.complete was never called"


# ------------------------------------------------------------------------ 3


@pytest.mark.anyio
async def test_decompose_async_keeps_loop_responsive(monkeypatch):
    from aja.planning.planner import Planner

    _forbid_run_async_synchronously(monkeypatch)
    gw = FakeAsyncGateway(delay=0.2)
    _patch_generator_planner(monkeypatch, gw)
    _install_completion_async(monkeypatch, gw)
    planner = Planner(gateway=gw)

    beats = {"count": 0}

    async def heartbeat():
        while True:
            await asyncio.sleep(0.02)
            beats["count"] += 1

    hb = asyncio.create_task(heartbeat())
    try:
        graph = await planner.decompose_async("Write a haiku about databases")
    finally:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass

    assert isinstance(graph, PlanGraph)
    assert gw.complete_calls, "fake gateway.complete was never called"
    # A ~200ms fake LLM round-trip must leave room for many heartbeats;
    # a blocked loop would tick zero times.
    assert beats["count"] >= 3


# ------------------------------------------------------------------------ 4


def test_sync_decompose_still_returns_plan_graph(monkeypatch):
    """Regression guard: the existing sync API must remain intact."""
    from unittest.mock import patch

    from aja.planning.planner import Planner

    # verify_plan (sync) calls aja.llm.completion -> get_gateway_for_model,
    # which resolves a REAL gateway and opens a live httpx2/AsyncOpenAI client.
    # That client's cleanup then crashes later anyio task groups with
    # ExceptionGroups of connection-close errors. Stub the whole LLM surface:
    # plan generation via _call_llm, verification via completion.
    def _fake_completion(prompt=None, system_prompt="", model=None,
                         temperature=None, tools=None, *args, **kwargs):
        sys_text = (system_prompt or "").lower()
        if "verifier" in sys_text or "verification" in sys_text or "logic" in sys_text:
            return json.dumps({"valid": True, "conflicts": [],
                               "missing_preconditions": []})
        return VALID_PLAN_RAW

    with patch("aja.planning.generator.retrieve_methods", return_value=[]), \
         patch("aja.planning.planner.Planner._call_llm") as mock_call_llm, \
         patch("aja.llm.completion", side_effect=_fake_completion), \
         patch("aja.llm.get_gateway_for_model") as _forbid_gw:
        _forbid_gw.side_effect = AssertionError(
            "real gateway resolution is forbidden in sync regression test"
        )
        mock_call_llm.side_effect = lambda *a, **k: VALID_PLAN_RAW

        planner = Planner(gateway=FakeAsyncGateway())
        graph = planner.decompose("Write a haiku about databases")

    assert isinstance(graph, PlanGraph)
    assert graph.goal == "Write a haiku about databases"


# ------------------------------------------------------------------------ 5


@pytest.mark.anyio
async def test_step_planning_awaits_instead_of_thread_spawning(monkeypatch):
    """goal_engine._step_planning must not spawn threads while planning."""
    from types import SimpleNamespace

    from aja.goals.goal_engine import Goal, GoalEngine
    from aja.planning.planner import _fallback_graph

    # Any LLM/gateway touch inside _step_planning must hit fakes, never the
    # network (real AsyncOpenAI/httpx2 clients leak across the closed loop).
    gw = FakeAsyncGateway()
    _install_completion_async(monkeypatch, gw)
    _forbid_real_gateways(monkeypatch)
    _forbid_run_async_synchronously(monkeypatch)

    engine = GoalEngine.__new__(GoalEngine)
    engine.memory = SimpleNamespace(record_scheduler_event=lambda **kw: None)
    engine.save_state = lambda: None

    async def fake_expand(goal):
        # The refactored path should simply await planning work on this loop.
        await asyncio.sleep(0.05)
        return _fallback_graph(goal.objective)

    monkeypatch.setattr(engine, "expand_goal", fake_expand)
    goal = Goal("write a sonnet about databases", priority=1)

    before = threading.active_count()
    await engine._step_planning(goal)
    after = threading.active_count()

    assert goal.plan is not None
    assert after == before, (
        f"planning spawned threads: {before} -> {after}"
    )
