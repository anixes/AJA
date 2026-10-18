"""Planning reliability tests: structured-planning recovery ladder and
worker output-contract capability caching."""

import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aja.llm_structured import StructuredOutputError
from aja.orchestration.adapters import (
    _mark_model_contract_capable,
    _model_supports_contracts,
    NativeWorkerAdapter,
)
import aja.orchestration.adapters as adapters_module
from aja.orchestration.swarm import SwarmEngine


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.fixture(autouse=True)
def _reset_contract_cache():
    adapters_module._MODEL_CONTRACT_CAPABLE.clear()
    yield
    adapters_module._MODEL_CONTRACT_CAPABLE.clear()


def _make_engine(monkeypatch):
    engine = SwarmEngine(dry_run=True, presenter=MagicMock())
    gateway = MagicMock()
    gateway.provider = "fake"
    gateway.chat = AsyncMock(return_value="synthesis ok")
    engine.gateway = gateway
    return engine


# ── Planning ladder ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_planning_ladder_recovers_after_prose(monkeypatch):
    engine = _make_engine(monkeypatch)
    valid_plan = [{"id": 1, "task": "step one"}, {"id": 2, "task": "step two"}]

    schemas_seen = []

    async def fake_structured(gateway, prompt, schema, **kwargs):
        schemas_seen.append(schema)
        if len(schemas_seen) == 1:
            raise StructuredOutputError("no parsable JSON found in model response")
        return valid_plan

    monkeypatch.setattr("aja.llm_structured.structured_completion", fake_structured)

    written = []
    monkeypatch.setattr(
        "aja.orchestration.swarm.write_baton", lambda p, d: written.append(dict(d))
    )
    dispatched = []
    async def fake_execute(baton_path):
        dispatched.append(baton_path.name)
        return {"status": "completed"}

    engine._execute_baton_worker = fake_execute
    await engine.plan_and_execute_batons("do the thing")

    assert len(schemas_seen) == 2, "second (ladder) attempt must happen"
    assert written[0]["task"] == "step one"
    assert written[1]["task"] == "step two"
    assert len(dispatched) == 2, "multi-step plan used; single-step NOT taken"


FULL_PLAN_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"id": {}, "task": {"type": "string"}},
        "required": ["id", "task"],
    },
}


@pytest.mark.anyio
async def test_planning_rung2_synthesizes_missing_ids(monkeypatch):
    engine = _make_engine(monkeypatch)

    async def fake_structured(gateway, prompt, schema, **kwargs):
        if schema.get("items", {}).get("required") == ["id", "task"]:
            raise StructuredOutputError("prose only")
        return [{"task": "alpha"}, {"task": "beta"}]

    monkeypatch.setattr("aja.llm_structured.structured_completion", fake_structured)

    plan = await engine._planning_recovery_ladder("prompt", FULL_PLAN_SCHEMA)
    assert plan == [
        {"id": 1, "task": "alpha"},
        {"id": 2, "task": "beta"},
    ]


@pytest.mark.anyio
async def test_planning_ladder_exhausted_falls_back_to_single_step(monkeypatch, caplog):
    engine = _make_engine(monkeypatch)
    engine.dry_run = False  # exercise the real single-step fallback, not the mock plan

    calls = {"n": 0}

    async def always_prose(gateway, prompt, schema, **kwargs):
        calls["n"] += 1
        raise StructuredOutputError("no parsable JSON found in model response")

    monkeypatch.setattr("aja.llm_structured.structured_completion", always_prose)

    written = []
    monkeypatch.setattr(
        "aja.orchestration.swarm.write_baton", lambda p, d: written.append(dict(d))
    )
    async def fake_execute(baton_path):
        return {"status": "completed"}

    engine._execute_baton_worker = fake_execute
    with caplog.at_level(logging.WARNING, logger="aja.orchestration.swarm"):
        await engine.plan_and_execute_batons("single objective")

    assert calls["n"] == 3, "initial + rung 1 + rung 2 attempts expected"
    assert any("recovery ladder exhausted" in r.message for r in caplog.records)
    assert len(written) == 1
    assert written[0]["id"] == "1"
    assert written[0]["task"] == "single objective"


# ── Contract capability cache ───────────────────────────────────────────────


class ContractFailingEngine:
    """Raises StructuredOutputError when given an output_contract kwarg."""

    def __init__(self, *args, **kwargs):
        self.presenter = MagicMock()
        self.calls = []

    async def execute_direct(self, task, output_contract=None):
        self.calls.append(output_contract)
        if output_contract is not None:
            raise StructuredOutputError("model returned prose")
        return {"status": "ok"}


@pytest.mark.anyio
async def test_contract_cache_skips_known_bad_model(monkeypatch):
    monkeypatch.setenv("AJA_WORKER_MODEL", "cache-test-model")
    stub = ContractFailingEngine()
    baton = {"id": "1", "task": "t1", "objective": "t1"}
    workspace = "."

    with patch("aja.orchestration.swarm.SwarmEngine", lambda *a, **k: stub), \
         patch.object(NativeWorkerAdapter, "_create_branch"), \
         patch.object(NativeWorkerAdapter, "_get_diff", return_value=""), \
         patch.object(NativeWorkerAdapter, "_run_tests", return_value=""):
        adapter = NativeWorkerAdapter()
        await adapter.run_async(dict(baton), workspace)
        await adapter.run_async(dict(baton, id="2"), workspace)

    assert _model_supports_contracts("cache-test-model") is False
    assert stub.calls[0] is not None, "first call attempts the contract"
    assert stub.calls[1] is None, "fallback within first task is plain"
    assert stub.calls[2] is None, "second task skips contract entirely"
    assert len(stub.calls) == 3


@pytest.mark.anyio
async def test_contract_cache_marks_capable_model_true(monkeypatch):
    monkeypatch.setenv("AJA_WORKER_MODEL", "cache-good-model")

    class GoodEngine:
        def __init__(self, *args, **kwargs):
            self.presenter = MagicMock()
            self.calls = []

        async def execute_direct(self, task, output_contract=None):
            self.calls.append(output_contract)
            if output_contract is not None:
                return {"result": {"summary": "done"}}
            return {"status": "ok"}

    stub = GoodEngine()
    baton = {"id": "1", "task": "t1", "objective": "t1"}
    workspace = "."

    with patch("aja.orchestration.swarm.SwarmEngine", lambda *a, **k: stub), \
         patch.object(NativeWorkerAdapter, "_create_branch"), \
         patch.object(NativeWorkerAdapter, "_get_diff", return_value=""), \
         patch.object(NativeWorkerAdapter, "_run_tests", return_value=""):
        adapter = NativeWorkerAdapter()
        await adapter.run_async(dict(baton), workspace)
        await adapter.run_async(dict(baton, id="2"), workspace)

    assert _model_supports_contracts("cache-good-model") is True
    assert all(c is not None for c in stub.calls), "contract attempted every task"
    assert len(stub.calls) == 2


@pytest.mark.anyio
async def test_model_supports_contracts_unknown_defaults_true():
    assert _model_supports_contracts("never-seen-model") is True
    _mark_model_contract_capable("never-seen-model", False)
    assert _model_supports_contracts("never-seen-model") is False
