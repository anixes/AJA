"""Unit tests for aja.assistant.briefing (daily briefing composer)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from aja.assistant.briefing import (
    BRIEFING_GOAL,
    compose_briefing,
    register_briefing_jobs,
    send_briefing,
)
from aja.cognitive.temporal_graph import BiTemporalEntityGraph
from aja.scheduler.cron_scheduler import CronScheduler


# --------------------------------------------------------------------------- doubles

class InMemoryStore:
    def __init__(self):
        self.tasks = {}

    def create_task(self, data):
        record = dict(data)
        if "metadata" in record:
            record["metadata_json"] = json.dumps(record.pop("metadata"))
        self.tasks[record["task_id"]] = record
        return dict(record)

    def get_task(self, task_id):
        task = self.tasks.get(task_id)
        return dict(task) if task else None

    def update_task(self, task_id, updates):
        if task_id not in self.tasks:
            raise KeyError(task_id)
        self.tasks[task_id].update(updates)
        return dict(self.tasks[task_id])

    def list_tasks(self, status=None, statuses=None, limit=50):
        allowed = statuses or ([status] if status else None)
        out = [
            dict(t)
            for t in self.tasks.values()
            if not allowed or t.get("status") in allowed
        ]
        return out[:limit]


class FakeEventSink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(dict(event))
        return "evt"


def make_memory(tasks):
    mem = MagicMock()
    mem.list_tasks.side_effect = lambda status=None, statuses=None, limit=50, **kw: [
        dict(t)
        for t in tasks
        if not statuses or t.get("status") in statuses
    ][:limit]
    return mem


def make_scheduler():
    return CronScheduler(store=InMemoryStore(), event_sink=FakeEventSink())


def seed_calendar(graph):
    now_utc = datetime.now(timezone.utc)
    start = now_utc + timedelta(hours=5)
    graph.upsert_entity(
        "calendar_event",
        "[cal] Dentist",
        {
            "title": "Dentist",
            "location": "Clinic",
            "start_iso": start.isoformat(),
            "end_iso": (start + timedelta(hours=1)).isoformat(),
        },
        valid_from=start.timestamp(),
    )
    graph.invalidate_entity(
        "calendar_event",
        "[cal] Dentist",
        invalid_at=(start + timedelta(hours=1)).timestamp(),
    )

    far = now_utc + timedelta(days=4)
    graph.upsert_entity(
        "calendar_event",
        "[cal] Far Future",
        {"title": "Far Future", "location": "", "start_iso": far.isoformat()},
        valid_from=far.timestamp(),
    )
    return now_utc


@pytest.fixture()
def seeded_tasks():
    now = datetime.now()
    return [
        {
            "title": "Submit tax forms",
            "status": "pending",
            "due_date": (now - timedelta(days=2)).strftime("%Y-%m-%d"),
            "priority": "urgent",
        },
        {
            "title": "Call recruiter",
            "status": "active",
            "due_date": now.strftime("%Y-%m-%d"),
            "priority": "high",
        },
        {
            "title": "Renew passport",
            "status": "pending",
            "due_date": (now + timedelta(days=10)).strftime("%Y-%m-%d"),
            "priority": "low",
        },
    ]


# --------------------------------------------------------------------------- compose

def test_compose_full_briefing(seeded_tasks, tmp_path):
    sched = make_scheduler()
    soon = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
    far = (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds")
    sched.add_job("Reminder: stretch legs", at=soon, reminder=True)
    sched.add_job("Reminder: distant", at=far, reminder=True)

    # Seed an overnight research report.
    job = sched.add_job("check python.org for changes", "0 3 * * *", research=True)
    meta = json.loads(sched.store.get_task(job["task_id"])["metadata_json"])
    meta["last_report"] = "Python 3.14 released with a new JIT." * 20
    meta["last_run"] = time.time() - 3600
    sched.store.update_task(job["task_id"], {"metadata_json": json.dumps(meta)})

    graph = BiTemporalEntityGraph(db_path=tmp_path / "graph.db")
    seed_calendar(graph)

    text = compose_briefing(
        memory=make_memory(seeded_tasks), scheduler=sched, graph=graph
    )

    assert text.startswith("🌅 AJA Briefing — ")
    assert "overdue ·" in text and "reminder" in text
    assert "## 🔴 Overdue" in text and "Submit tax forms" in text and "2 day(s) late" in text
    assert "## 📅 Today" in text and "Call recruiter" in text
    assert "Dentist" in text and "Clinic" in text
    assert "Far Future" not in text
    assert "## ⏰ Reminders" in text and "stretch legs" in text and "distant" not in text
    assert "## 🎯 Priority Focus" in text and "tier)" in text
    assert "## 🌍 Overnight Research" in text and "check python.org for changes" in text


def test_counts_line_values(seeded_tasks):
    sched = make_scheduler()
    sched.add_job(
        "Reminder: one", at=(datetime.now() + timedelta(hours=1)).isoformat(), reminder=True
    )
    text = compose_briefing(memory=make_memory(seeded_tasks), scheduler=sched)
    header = text.splitlines()[2]
    assert header.strip("> ") == "1 overdue · 1 today · 1 reminder"


def test_empty_briefing_omits_sections():
    sched = make_scheduler()
    text = compose_briefing(memory=make_memory([]), scheduler=sched)
    assert "🌅 AJA Briefing — " in text
    assert "0 overdue · 0 today · 0 reminders" in text
    assert "🔴" not in text
    assert "📅" not in text
    assert "⏰" not in text


def test_ingredient_failure_degrades_gracefully(seeded_tasks):
    broken_mem = MagicMock()
    broken_mem.list_tasks.side_effect = RuntimeError("lancedb down")
    sched = make_scheduler()
    text = compose_briefing(memory=broken_mem, scheduler=sched)
    assert "## 🔴 Overdue\n(unavailable)" in text
    # Priority engine also failed -> section marked unavailable.
    assert "(unavailable)" in text.split("## 🎯 Priority Focus")[1].splitlines()[1]


def test_include_news_false_hides_research_section(seeded_tasks):
    sched = make_scheduler()
    text = compose_briefing(
        memory=make_memory(seeded_tasks), scheduler=sched, include_news=False
    )
    assert "🌍 Overnight Research" not in text


def test_stale_research_report_excluded(seeded_tasks):
    sched = make_scheduler()
    job = sched.add_job("research something", "0 3 * * *", research=True)
    meta = json.loads(sched.store.get_task(job["task_id"])["metadata_json"])
    meta["last_report"] = "old news"
    meta["last_run"] = time.time() - 3 * 86400
    sched.store.update_task(job["task_id"], {"metadata_json": json.dumps(meta)})

    text = compose_briefing(memory=make_memory(seeded_tasks), scheduler=sched)
    assert "old news" not in text


# --------------------------------------------------------------------------- delivery

def test_send_briefing_publishes_bus_payload(seeded_tasks):
    from aja.runtime.event_bus import bus, EVENTS

    captured = []

    def _capture(payload):
        captured.append(payload)

    event_type = EVENTS["MISSION_COMPLETED"]
    bus.subscribe(event_type, _capture)
    try:
        out = send_briefing(memory=make_memory(seeded_tasks), scheduler=make_scheduler())
    finally:
        bus.unsubscribe(event_type, _capture)

    assert len(captured) == 1
    assert captured[0]["message"] == out
    assert captured[0]["message"].startswith("🌅 AJA Briefing — ")


# --------------------------------------------------------------------------- registration

def test_register_briefing_jobs_creates_and_is_idempotent():
    sched = make_scheduler()

    first = register_briefing_jobs(sched)
    assert len(first) == 2
    metas = {}
    for j in first:
        meta = json.loads(sched.store.get_task(j["task_id"])["metadata_json"])
        metas[meta["slot"]] = meta
    assert metas["morning"]["briefing"] is True
    assert metas["evening"]["briefing"] is True
    assert metas["morning"]["schedule_expr"] == "0 7 * * *"
    assert all(sched.store.get_task(j["task_id"])["context"] == BRIEFING_GOAL for j in first)

    second = register_briefing_jobs(sched)
    assert second == []
    briefing_jobs = [
        (goal, meta)
        for goal, meta in (
            (t["context"], json.loads(t["metadata_json"]))
            for t in sched.store.list_tasks(statuses=["scheduled"])
        )
        if meta.get("briefing")
    ]
    assert len(briefing_jobs) == 2


def test_register_briefing_jobs_custom_cron():
    sched = make_scheduler()
    created = register_briefing_jobs(sched, morning="30 6 * * *", evening="45 20 * * *")
    exprs = sorted(
        json.loads(sched.store.get_task(j["task_id"])["metadata_json"])["schedule_expr"]
        for j in created
    )
    assert exprs == ["30 6 * * *", "45 20 * * *"]


# --------------------------------------------------------------------------- cron branch

def test_execute_job_routes_briefing_without_swarm_engine(seeded_tasks, monkeypatch):
    from aja.runtime.event_bus import bus, EVENTS

    sched = make_scheduler()
    job = sched.add_job(BRIEFING_GOAL, "0 7 * * *", briefing=True, slot="morning")
    job_id = job["task_id"]

    # Mark the job as mid-run like tick_loop does before spawning _execute_job.
    meta = json.loads(sched.store.get_task(job_id)["metadata_json"])
    meta["active_run_id"] = "run-x"
    meta["active_trace_id"] = "trace-x"
    sched.store.update_task(job_id, {"metadata_json": json.dumps(meta)})

    # If the briefing branch is skipped, SwarmEngine would be imported/constructed.
    boom = ModuleType("aja.orchestration.swarm")

    class _Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("SwarmEngine must not be constructed for briefings")

    boom.SwarmEngine = _Boom
    monkeypatch.setitem(sys.modules, "aja.orchestration.swarm", boom)

    captured = []
    calls = []

    def _capture(payload):
        captured.append(payload)

    def _fake_send(**kw):
        calls.append(kw)
        bus.publish(event_type, {"message": "rendered"})
        return "rendered"

    monkeypatch.setattr("aja.assistant.briefing.send_briefing", _fake_send)

    event_type = EVENTS["MISSION_COMPLETED"]
    bus.subscribe(event_type, _capture)
    try:
        asyncio.run(sched._execute_job(job_id, BRIEFING_GOAL, "run-x", "trace-x"))
    finally:
        bus.unsubscribe(event_type, _capture)

    assert len(calls) == 1
    assert len(captured) == 1 and captured[0]["message"] == "rendered"
    cleared = json.loads(sched.store.get_task(job_id)["metadata_json"])
    assert "active_run_id" not in cleared and "active_trace_id" not in cleared


# --------------------------------------------------------------------------- smoke

def test_rendered_example_fixture_snapshot(seeded_tasks, tmp_path):
    sched = make_scheduler()
    sched.add_job(
        "Reminder: standup notes",
        at=(datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds"),
        reminder=True,
    )
    graph = BiTemporalEntityGraph(db_path=tmp_path / "g.db")
    seed_calendar(graph)
    text = compose_briefing(memory=make_memory(seeded_tasks), scheduler=sched, graph=graph)

    lines = text.splitlines()
    assert lines[0].startswith("🌅 AJA Briefing — ")
    assert any(line.startswith("> ") and line.count("·") == 2 for line in lines)
