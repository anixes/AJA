import asyncio
import json
from datetime import datetime, timedelta

import pytest

from aja.scheduler.cron_scheduler import CronScheduler, create_reminder


class InMemoryStore:
    """Minimal RuntimeTaskStore double."""

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


@pytest.fixture()
def sched():
    return CronScheduler(
        check_interval=0.05,
        store=InMemoryStore(),
        event_sink=FakeEventSink(),
    )


def _meta(sched, job_id):
    return json.loads(sched.store.get_task(job_id)["metadata_json"])


# ---------------------------------------------------------------- one-shot API

def test_add_one_shot_iso_datetime(sched):
    run_at = datetime(2026, 12, 24, 18, 30)
    job = sched.add_job("Deploy the tree", at=run_at.isoformat())
    meta = _meta(sched, job["task_id"])
    assert meta["one_shot"] is True
    assert meta["run_at"] == run_at.isoformat()
    assert meta["schedule_expr"] == f"at:{run_at.isoformat()}"


def test_add_one_shot_nl_string(sched):
    job = sched.add_job("Water plants", at="in 2 hours")
    meta = _meta(sched, job["task_id"])
    assert meta["one_shot"] is True
    run_dt = datetime.fromisoformat(meta["run_at"])
    expected = datetime.now().replace(second=run_dt.second, microsecond=0) + timedelta(hours=2)
    assert abs((run_dt - expected).total_seconds()) < 60


def test_add_job_still_validates_cron(sched):
    with pytest.raises(ValueError):
        sched.add_job("Bad job", "not-a-cron")


async def _run_ticks(sched, seconds=0.35):
    # Drive tick_loop directly to avoid start()'s slow projection rebuild.
    sched._running = True
    task = asyncio.create_task(sched.tick_loop())
    try:
        await asyncio.sleep(seconds)
    finally:
        await sched.stop_async()


# ------------------------------------------------------------- reminder firing

def test_reminder_fires_and_auto_deletes(sched):
    from aja.runtime.event_bus import bus, EVENTS

    past = (datetime.now() - timedelta(seconds=5)).isoformat()
    job = sched.add_job(
        "call mom", at=past, reminder=True, chat_id="chat-1", platform="telegram"
    )

    payloads = []

    def _capture(payload):
        payloads.append(payload)

    event_type = EVENTS["MISSION_COMPLETED"]
    bus.subscribe(event_type, _capture)
    try:
        asyncio.run(_run_ticks(sched))
    finally:
        bus.unsubscribe(event_type, _capture)

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["message"] == "⏰ Reminder: call mom"
    assert payload["job_id"] == job["task_id"]
    assert payload["chat_id"] == "chat-1"
    assert payload["platform"] == "telegram"

    # auto-deleted after firing
    assert sched.store.get_task(job["task_id"])["status"] == "archived"
    assert sched.list_jobs() == []


def test_non_reminder_one_shot_disables_not_deletes(sched, monkeypatch):
    fired = []

    async def _fake_execute(job_id, goal, run_id, trace_id):
        fired.append(goal)

    monkeypatch.setattr(sched, "_execute_job", _fake_execute)

    past = (datetime.now() - timedelta(seconds=5)).isoformat()
    job = sched.add_job("Run nightly backup", at=past)

    asyncio.run(_run_ticks(sched))

    assert fired == ["Run nightly backup"]
    stored = sched.store.get_task(job["task_id"])
    assert stored["status"] == "disabled"
    # Disabled jobs leave the active list -> never refires
    assert all(j["job_id"] != job["task_id"] for j in sched.list_jobs())


def test_future_one_shot_does_not_fire_early(sched):
    from aja.runtime.event_bus import bus, EVENTS

    future = (datetime.now() + timedelta(hours=1)).isoformat()
    job = sched.add_job("call mom", at=future, reminder=True)

    payloads = []
    event_type = EVENTS["MISSION_COMPLETED"]

    def _capture(payload):
        payloads.append(payload)

    bus.subscribe(event_type, _capture)
    try:
        asyncio.run(_run_ticks(sched, seconds=0.2))
    finally:
        bus.unsubscribe(event_type, _capture)

    assert payloads == []
    assert sched.store.get_task(job["task_id"])["status"] == "scheduled"


def test_reminder_cleanup_disabled_keeps_job(sched):
    past = (datetime.now() - timedelta(seconds=5)).isoformat()
    job = sched.add_job("stretch legs", at=past, reminder=True, cleanup_after_fire=False)

    asyncio.run(_run_ticks(sched))

    assert sched.store.get_task(job["task_id"])["status"] == "disabled"
    assert sched.store.get_task(job["task_id"]) is not None


# ------------------------------------------------------------------- snooze

def test_snooze_reschedules_reminder(sched):
    run_at = (datetime.now() + timedelta(minutes=30)).isoformat()
    job = sched.add_job("call mom", at=run_at, reminder=True)

    assert sched.snooze_reminder(job["task_id"], 10) is True

    meta = _meta(sched, job["task_id"])
    new_run_at = datetime.fromisoformat(meta["run_at"])
    expected = datetime.now().replace(microsecond=0) + timedelta(minutes=10)
    assert abs((new_run_at - expected).total_seconds()) < 5
    stored = sched.store.get_task(job["task_id"])
    assert stored["status"] == "scheduled"
    assert meta["schedule_expr"].startswith("at:")


def test_snooze_rejects_invalid(sched):
    run_at = (datetime.now() + timedelta(hours=1)).isoformat()
    recurring = sched.add_job("Daily sync", "0 9 * * *")

    assert sched.snooze_reminder(recurring["task_id"], 10) is False

    job = sched.add_job("call mom", at=run_at, reminder=True)
    assert sched.snooze_reminder(job["task_id"], 0) is False
    assert sched.snooze_reminder("JOB-NOPE", 5) is False


# ------------------------------------------------------------ create_reminder

def test_create_reminder_happy_path():
    sched = CronScheduler(store=InMemoryStore(), event_sink=FakeEventSink())
    job = create_reminder(
        "file taxes",
        "tomorrow 9am",
        chat_id=42,
        platform="discord",
        scheduler=sched,
    )
    assert job is not None
    meta = json.loads(sched.store.get_task(job["task_id"])["metadata_json"])
    assert meta["reminder"] is True
    assert meta["chat_id"] == 42
    assert meta["platform"] == "discord"
    assert sched.store.get_task(job["task_id"])["context"] == "Reminder: file taxes"

    run_dt = datetime.fromisoformat(meta["run_at"])
    tomorrow = datetime.now() + timedelta(days=1)
    assert (run_dt.date() - tomorrow.date()).days in (0, 1)
    assert run_dt.hour == 9 and run_dt.minute == 0


def test_create_reminder_unparseable_time_returns_none():
    sched = CronScheduler(store=InMemoryStore(), event_sink=FakeEventSink())
    assert create_reminder("do stuff", "eventually maybe", scheduler=sched) is None
    assert create_reminder("do stuff", "", scheduler=sched) is None


# ----------------------------------------------------------- intent routing

def test_intent_remind_me_routes_to_scheduler_shape():
    from aja.interface.intent_parser import local_router_fallback

    res = local_router_fallback("remind me to call mom at 5pm")
    assert res is not None
    assert res["type"] == "reminder"
    assert res["task"] == "call mom"
    assert res["when_raw"] == "at 5pm"

    res = local_router_fallback("remind me buy milk tomorrow 9am")
    assert res["type"] == "reminder"
    assert res["task"] == "buy milk"
    assert res["when_raw"] == "tomorrow 9am"

    res = local_router_fallback("Remind me to stretch in 30 minutes")
    assert res["type"] == "reminder"
    assert res["when_raw"] == "in 30 minutes"


def test_intent_reminder_without_time_not_captured():
    from aja.interface.intent_parser import local_router_fallback

    # No time anchor -> must fall through (LLM), not produce a broken reminder
    assert local_router_fallback("remind me to call mom later please") is None


def test_intent_snooze_regex():
    from aja.interface.intent_parser import local_router_fallback

    res = local_router_fallback("snooze that reminder for 10 minutes")
    assert res is not None
    assert res["type"] == "reminder_snooze"
    assert res["minutes"] == 10

    res = local_router_fallback("snooze the reminder 2h")
    assert res["type"] == "reminder_snooze"
    assert res["minutes"] == 120

    res = local_router_fallback("snooze reminder 5 min")
    assert res["minutes"] == 5
