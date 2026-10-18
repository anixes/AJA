"""Hermetic unit tests for scheduled autonomous research missions.

Covers: research-goal detection heuristics, env-configurable job timeout,
report capture + bus delivery, legacy path preservation for non-research
goals, and the StructuredOutputError contract fallback.
"""
import asyncio
import json
import sys
import types

import pytest

import aja.scheduler.cron_scheduler as cron_scheduler
from aja.scheduler.cron_scheduler import (
    CronScheduler,
    get_job_timeout,
    is_research_goal,
)


class FakeStore:
    """Minimal in-memory RuntimeTaskStore double."""

    def __init__(self):
        self._tasks = {}

    def create_task(self, data):
        tid = data["task_id"]
        self._tasks[tid] = {
            "task_id": tid,
            "context": data["context"],
            "owner": data.get("owner", "scheduler"),
            "status": data.get("status", "scheduled"),
            "metadata_json": json.dumps(data.get("metadata", {})),
        }
        return dict(self._tasks[tid])

    def get_task(self, task_id):
        task = self._tasks.get(task_id)
        return dict(task) if task else None

    def update_task(self, task_id, updates):
        if task_id not in self._tasks:
            return None
        self._tasks[task_id].update(updates)
        return dict(self._tasks[task_id])

    def list_tasks(self, statuses=None, status=None):
        wanted = statuses or ([status] if status else None)
        return [
            dict(t)
            for t in self._tasks.values()
            if not wanted or t["status"] in wanted
        ]


class FakeSink:
    def __init__(self):
        self.events = []

    def emit(self, payload):
        self.events.append(payload)


class FakeEngine:
    def __init__(self, report="All systems nominal.", fail_contract=False):
        self.report = report
        self.fail_contract = fail_contract
        self.contract_calls = []
        self.plain_calls = []

    async def execute_direct(
        self, objective, session_history=None, interactive=True,
        output_contract=None,
    ):
        if output_contract:
            self.contract_calls.append(objective)
            if self.fail_contract:
                from aja.llm_structured import StructuredOutputError

                raise StructuredOutputError("model emitted non-JSON")
            return {"status": "completed", "result": {"summary": self.report}}
        self.plain_calls.append(objective)
        return {"status": "completed"}

    async def plan_and_execute_batons(self, goal):
        self.plain_calls.append(goal)
        return {"status": "completed"}


@pytest.fixture
def fake_bus(monkeypatch):
    """Installs a fake aja.runtime.event_bus module and returns published."""
    published = []

    bus_obj = types.SimpleNamespace(
        publish=lambda event_type, payload: published.append((event_type, payload))
    )
    mod = types.ModuleType("aja.runtime.event_bus")
    mod.bus = bus_obj
    mod.EVENTS = {
        "MISSION_RESULT": "MISSION_RESULT",
        "MISSION_COMPLETED": "MISSION_COMPLETED",
    }
    monkeypatch.setitem(sys.modules, "aja.runtime.event_bus", mod)
    return published


def make_scheduler(monkeypatch, engine):
    monkeypatch.setattr("aja.orchestration.swarm.SwarmEngine", lambda: engine)
    settings = types.SimpleNamespace(direct_execution=True)
    fake_config = types.SimpleNamespace(swarm_settings=settings)
    import aja.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG", fake_config)

    sink = FakeSink()
    sched = CronScheduler(store=FakeStore(), event_sink=sink)
    return sched, sink


def test_timeout_default_and_bad_env(monkeypatch):
    monkeypatch.delenv("AJA_JOB_TIMEOUT_S", raising=False)
    assert get_job_timeout() == 600.0
    monkeypatch.setenv("AJA_JOB_TIMEOUT_S", "not-a-number")
    assert get_job_timeout() == 600.0
    monkeypatch.setenv("AJA_JOB_TIMEOUT_S", "-5")
    assert get_job_timeout() == 600.0


def test_research_goal_heuristics():
    positives = [
        "search for the latest python release",
        "research quantum computing trends",
        "monitor disk usage",
        "check system health",
        "summarize yesterday's incident",
        "fetch the status page",
        "collect AI news",
        "write a report on latency",
        "what changed in the latest build?",
        "track changes upstream",
    ]
    for g in positives:
        assert is_research_goal(g), g
    negatives = [
        "deploy the service to production",
        "refactor auth module",
        "git commit and push",
    ]
    for g in negatives:
        assert not is_research_goal(g), g


def test_research_goal_metadata_override():
    # Explicit flag forces research even without keywords.
    assert is_research_goal("deploy to prod", {"research": True}) is True
    assert is_research_goal("deploy to prod", {}) is False
    assert is_research_goal("deploy to prod") is False


def test_add_job_metadata_passthrough():
    store = FakeStore()
    sched = CronScheduler(store=store, event_sink=FakeSink())
    job = sched.add_job("deploy to prod", "* * * * *", research=True)
    stored = store.get_task(job["task_id"])
    meta = json.loads(stored["metadata_json"])
    assert meta.get("research") is True


def test_timeout_uses_env_value(monkeypatch):
    observed = {}
    real_wait_for = asyncio.wait_for

    async def spy_wait_for(aw, timeout=None, **kwargs):
        observed.setdefault("timeouts", []).append(timeout)
        return await real_wait_for(aw, timeout=timeout, **kwargs)

    engine = FakeEngine()
    sched, sink = make_scheduler(monkeypatch, engine)
    monkeypatch.setattr(cron_scheduler.asyncio, "wait_for", spy_wait_for)
    monkeypatch.setenv("AJA_JOB_TIMEOUT_S", "42")

    asyncio.run(sched._execute_job("JOB-X", "run the test suite", "r1", "t1"))
    assert observed["timeouts"], "wait_for was never invoked"
    assert all(t == 42.0 for t in observed["timeouts"])
    assert get_job_timeout() == 42.0


def test_non_research_goal_keeps_legacy_path(monkeypatch):
    engine = FakeEngine()
    sched, sink = make_scheduler(monkeypatch, engine)

    asyncio.run(sched._execute_job("JOB-D", "refactor auth module now", "r1", "t1"))

    # Legacy path: plain execute_direct call, no contract attempt, no report.
    assert len(engine.plain_calls) == 1
    assert engine.contract_calls == []
    kinds = [e["event_type"] for e in sink.events]
    assert "SCHEDULER_JOB_SUCCESS" in kinds
    assert "SCHEDULER_JOB_REPORT" not in kinds


def test_research_job_captures_report_and_publishes(monkeypatch, fake_bus):
    engine = FakeEngine(report="Stable Python version is 3.14.")
    sched, sink = make_scheduler(monkeypatch, engine)
    job = sched.add_job("search for the latest python release notes", "* * * * *")

    asyncio.run(
        sched._execute_job(
            job["task_id"], "search for the latest python release notes", "r1", "t1"
        )
    )

    # Contract path used exactly once; no plain fallback needed.
    assert len(engine.contract_calls) == 1
    assert engine.plain_calls == []

    # Journal event emitted with the report.
    report_events = [
        e for e in sink.events if e["event_type"] == "SCHEDULER_JOB_REPORT"
    ]
    assert len(report_events) == 1
    assert report_events[0]["metadata"]["report"] == "Stable Python version is 3.14."

    # Bus delivery payload carries job_id/goal/report and message text.
    delivered = [(k, p) for k, p in fake_bus if k == "MISSION_COMPLETED"]
    assert len(delivered) == 1
    _, payload = delivered[0]
    assert payload["job_id"] == job["task_id"]
    assert payload["report"] == "Stable Python version is 3.14."
    assert "Stable Python version is 3.14." in payload["message"]

    # Report persisted as last_report on the job record.
    meta = json.loads(sched.store.get_task(job["task_id"])["metadata_json"])
    assert meta.get("last_report") == "Stable Python version is 3.14."


def test_contract_fallback_to_plain_call(monkeypatch, fake_bus):
    engine = FakeEngine(fail_contract=True)
    sched, sink = make_scheduler(monkeypatch, engine)

    asyncio.run(
        sched._execute_job("JOB-F", "monitor upstream changes daily", "r1", "t1")
    )

    # Contract attempted once, then graceful plain fallback.
    assert len(engine.contract_calls) == 1
    assert len(engine.plain_calls) == 1

    # Mission still completes successfully without a captured report.
    kinds = [e["event_type"] for e in sink.events]
    assert "SCHEDULER_JOB_SUCCESS" in kinds
    assert "SCHEDULER_JOB_ERROR" not in kinds
