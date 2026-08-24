"""Regression tests for scheduler bug fixes.

Covers:
- Bug 1: projection rebuild preserves unknown metadata keys
- Bug 2: tick loop fetches beyond the store's default limit of 50
- Bug 3: match_cron_expr matches operator-local wall-clock times
- Bug 5: snooze cannot resurrect fired-and-cleaned-up reminders
"""
import asyncio
import json
import types
from datetime import datetime

from aja.scheduler.cron_scheduler import CronScheduler, match_cron_expr
from aja.runtime.scheduler_journal import (
    SchedulerJournal,
    rebuild_scheduler_projections,
)


class FakeStore:
    """In-memory RuntimeTaskStore double honoring the ``limit`` kwarg."""

    def __init__(self):
        self._tasks = {}
        self.last_list_limit = None
        self.last_list_count = 0

    def create_task(self, data):
        tid = data["task_id"]
        self._tasks[tid] = {
            "task_id": tid,
            "title": data.get("title", ""),
            "context": data["context"],
            "owner": data.get("owner", "scheduler"),
            "status": data.get("status", "scheduled"),
            "metadata_json": json.dumps(data.get("metadata", {})),
            "created_at": "",
            "due_date": "",
            "completion_note": "",
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

    def list_tasks(self, statuses=None, status=None, limit=50):
        self.last_list_limit = limit
        wanted = statuses or ([status] if status else None)
        matches = [
            dict(t) for t in self._tasks.values()
            if not wanted or t["status"] in wanted
        ]
        trimmed = matches[:limit]
        self.last_list_count = len(trimmed)
        return trimmed


class FakeSink:
    def emit(self, payload):
        pass


def make_scheduler(store=None):
    return CronScheduler(store=store or FakeStore(), event_sink=FakeSink())


# ---------------------------------------------------------------- Bug 1


def test_rebuild_preserves_unknown_metadata_keys(monkeypatch):
    """Projection rebuild must MERGE, not replace, existing row metadata."""
    store = FakeStore()
    sched = make_scheduler(store)
    job = sched.add_job(
        "ping the ops channel",
        at="2100-01-01T09:00:00",
        reminder=True,
        cleanup_after_fire=True,
        chat_id="chat-123",
        platform="telegram",
    )

    # Pre-seed the table with the row as create_task wrote it (full metadata
    # incl. unknown keys) — simulates a job that already lives in LanceDB.
    rows = {}
    stored_meta = json.loads(
        store.get_task(job["task_id"])["metadata_json"]
    )
    rows[job["task_id"]] = {
        "task_id": job["task_id"],
        "status": "scheduled",
        "metadata_json": json.dumps(stored_meta),
    }

    class FakeTable:
        def search(self):
            return self

        def where(self, predicate):
            self._pred = predicate
            return self

        def limit(self, n):
            return self

        def to_list(self):
            tid = self._pred.split("'")[1]
            row = rows.get(tid)
            return [dict(row)] if row else []

        def update(self, where, values):
            tid = where.split("'")[1]
            rows.setdefault(tid, {}).update(values)

        def add(self, new_rows):
            for r in new_rows:
                rows[r["task_id"]] = dict(r)

    class FakeDB:
        @staticmethod
        def open_table(name):
            return FakeTable()

    fake_lance = types.SimpleNamespace(
        memory=types.SimpleNamespace(db=FakeDB())
    )
    import aja.runtime.lance_stores as lance_mod

    monkeypatch.setattr(lance_mod, "LanceRuntimeStore", lambda: fake_lance)

    # Journal events (register then fire) trigger projection rebuilds.
    SchedulerJournal().emit(
        "SCHEDULER_JOB_REGISTERED",
        {
            "job_id": job["task_id"],
            "goal": "ping the ops channel",
            "schedule_expr": stored_meta["schedule_expr"],
        },
    )
    SchedulerJournal().emit(
        "SCHEDULER_JOB_FIRED",
        {
            "job_id": job["task_id"],
            "goal": "ping the ops channel",
            "run_id": "r-test",
        },
    )

    rebuild_scheduler_projections(job["task_id"])

    stored = rows[job["task_id"]]
    meta = json.loads(stored["metadata_json"])
    # Reducer-computed keys present...
    assert meta["schedule_expr"].startswith("at:")
    assert meta["paused"] is False
    # ...and unknown keys survive the rebuild.
    assert meta.get("reminder") is True
    assert meta.get("cleanup_after_fire") is True
    assert meta.get("chat_id") == "chat-123"
    assert meta.get("platform") == "telegram"
    assert meta.get("one_shot") is True
    assert meta.get("run_at") == "2100-01-01T09:00:00"


# ---------------------------------------------------------------- Bug 2


def test_tick_loop_sees_jobs_past_default_limit():
    """The tick loop must not starve jobs hidden behind the default limit."""
    store = FakeStore()
    sched = make_scheduler(store)

    for i in range(60):
        store.create_task({
            "task_id": f"JOB-FILL{i:03d}",
            "context": f"filler job {i}",
            "owner": "scheduler",
            "status": "scheduled",
            "metadata": {
                # Never-matching cron keeps every job dormant this tick;
                # the assertion is about visibility, not firing.
                "schedule_expr": "0 0 31 2 *",
                "last_run_tick": 0,
            },
        })

    async def main():
        sched._running = True
        t = asyncio.create_task(sched.tick_loop())
        await asyncio.sleep(0.15)
        sched._running = False
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    asyncio.run(main())

    assert store.last_list_limit == 10000
    assert store.last_list_count == 60


# ---------------------------------------------------------------- Bug 3


def test_match_cron_expr_uses_local_time():
    """A naive-local datetime must match the operator's local cron spec."""
    local_3pm = datetime(2026, 8, 24, 15, 0)
    assert match_cron_expr("0 15 * * *", local_3pm) is True
    assert match_cron_expr("0 14 * * *", local_3pm) is False


# ---------------------------------------------------------------- Bug 5


def test_snooze_refuses_resurrecting_cleaned_up_reminder():
    store = FakeStore()
    sched = make_scheduler(store)
    store.create_task({
        "task_id": "JOB-GONE",
        "context": "old reminder",
        "status": "archived",
        "metadata": {
            "one_shot": True,
            "cleanup_after_fire": True,
            "run_at": "2026-08-23T09:00:00",
        },
    })
    assert sched.snooze_reminder("JOB-GONE", 10) is False
    assert store.get_task("JOB-GONE")["status"] == "archived"


def test_snooze_still_works_for_live_one_shot():
    store = FakeStore()
    sched = make_scheduler(store)
    store.create_task({
        "task_id": "JOB-LIVE",
        "context": "live reminder",
        "status": "disabled",
        "metadata": {"one_shot": True, "run_at": "2026-08-23T09:00:00"},
    })
    assert sched.snooze_reminder("JOB-LIVE", 5) is True
    stored = store.get_task("JOB-LIVE")
    assert stored["status"] == "scheduled"
    assert json.loads(stored["metadata_json"])["run_at"].startswith("20")
