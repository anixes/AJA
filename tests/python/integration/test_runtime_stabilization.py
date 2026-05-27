import asyncio
import json
import shutil
import uuid
from pathlib import Path

from aja.capabilities.handover import HandoverCapability
from aja.runtime.event_bus import EventBus
from aja.runtime.event_bus import bus as global_bus
from aja.runtime.baton_types import (
    MISSION_BATON_FIELDS,
    WORKER_BATON_FIELDS,
    MissionBatonPayload,
    WorkerBatonPayload,
)
from aja.runtime.handover import (
    _IN_MEMORY_BATONS,
    _MAX_IN_MEMORY_BATONS,
    BatonManager,
)
from aja.runtime.events import normalize_runtime_event
from aja.runtime.session import SessionManager
from aja.runtime.lancedb_logger import LanceDBLogger
from aja.scheduler.cron_scheduler import CronScheduler


class FakeTaskStore:
    def __init__(self):
        self.tasks = {}

    def create_task(self, data):
        row = {
            "task_id": data["task_id"],
            "context": data["context"],
            "owner": data["owner"],
            "status": data["status"],
            "metadata_json": json.dumps(data.get("metadata", {})),
        }
        self.tasks[row["task_id"]] = row
        return row

    def get_task(self, task_id):
        return self.tasks.get(task_id)

    def update_task(self, task_id, updates):
        self.tasks[task_id].update(updates)
        return self.tasks[task_id]

    def list_tasks(self, status=None, statuses=None, limit=50):
        rows = list(self.tasks.values())
        if status:
            rows = [row for row in rows if row["status"] == status]
        if statuses:
            rows = [row for row in rows if row["status"] in statuses]
        return rows[:limit]


class FakeEventSink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)
        return f"evt-{len(self.events)}"


class SlowScheduler(CronScheduler):
    async def _execute_job(self, job_id, goal, run_id, trace_id):
        try:
            await asyncio.sleep(5)
        finally:
            self._running_jobs.discard(job_id)
            self._execution_tasks.pop(job_id, None)


def test_handover_capability_uses_runtime_baton_api():
    cap = HandoverCapability()
    baton_dir = Path("libs/aja-core/temp_batons") / f"cap-test-{uuid.uuid4().hex}"
    baton_dir.mkdir(parents=True, exist_ok=True)
    cap.manager.baton_dir = baton_dir

    try:
        result = cap.execute(
            {
                "action": "generate",
                "objective": "handover capability test",
                "current_state": {
                    "run_id": "cap-test-run",
                    "history": [{"role": "user", "content": "hello"}],
                    "metadata": {"source": "capability-test"},
                },
            }
        )

        assert result.success is True
        code = result.output["code"]

        pickup = cap.execute({"action": "pickup", "code": code})
        assert pickup.success is True
        assert pickup.output["state"]["run_id"] == "cap-test-run"
        assert pickup.output["state"]["metadata"]["source"] == "capability-test"
    finally:
        shutil.rmtree(baton_dir, ignore_errors=True)


def test_cron_scheduler_tracks_and_cancels_owned_execution_tasks():
    async def scenario():
        store = FakeTaskStore()
        sink = FakeEventSink()
        sched = SlowScheduler(check_interval=0.01, store=store, event_sink=sink)
        sched.add_job("slow scheduled work", "every 1s")

        sched.start()
        await asyncio.sleep(0.05)

        assert len(sched._execution_tasks) == 1
        assert len(sched._running_jobs) == 1
        assert any(event["event_type"] == "SCHEDULER_JOB_DUE" for event in sink.events)

        await sched.stop_async()

        assert sched._execution_tasks == {}
        assert sched._running_jobs == set()

    asyncio.run(scenario())


def test_event_bus_isolates_handler_failures_and_can_reset():
    bus = EventBus()
    seen = []

    def bad_handler(_payload):
        raise RuntimeError("boom")

    def good_handler(payload):
        seen.append(payload["value"])

    bus.subscribe("TEST_EVENT", bad_handler)
    bus.subscribe("TEST_EVENT", good_handler)

    bus.publish("TEST_EVENT", {"value": 42})
    assert seen == [42]

    assert bus.unsubscribe("TEST_EVENT", good_handler) is True
    bus.publish("TEST_EVENT", {"value": 99})
    assert seen == [42]

    bus.reset()
    assert bus.subscribers == {}


def test_event_bus_subscribe_once_prevents_duplicate_handlers():
    bus = EventBus()
    seen = []

    def handler(payload):
        seen.append(payload["value"])

    bus.subscribe_once("ONCE_EVENT", handler, "stable-key")
    bus.subscribe_once("ONCE_EVENT", handler, "stable-key")

    bus.publish("ONCE_EVENT", {"value": 7})

    assert seen == [7]
    assert len(bus.subscribers["ONCE_EVENT"]) == 1


def test_lancedb_logger_uses_idempotent_event_subscriptions():
    class FakeSink:
        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)

    old_subscribers = {key: list(value) for key, value in global_bus.subscribers.items()}
    old_keys = set(global_bus._subscription_keys)
    try:
        global_bus.reset()
        sink = FakeSink()

        LanceDBLogger(event_sink=sink)
        LanceDBLogger(event_sink=sink)

        global_bus.publish("NODE_SUCCESS", {"node_id": "n1", "message": "done"})

        assert len(global_bus.subscribers["NODE_SUCCESS"]) == 1
        assert len(sink.events) == 1
        assert sink.events[0]["event_type"] == "NODE_SUCCESS"
    finally:
        global_bus.subscribers.clear()
        global_bus.subscribers.update(old_subscribers)
        global_bus._subscription_keys.clear()
        global_bus._subscription_keys.update(old_keys)


def test_runtime_event_normalization_preserves_runtime_context():
    event = normalize_runtime_event(
        {
            "event_type": "SCHEDULER_JOB_SUCCESS",
            "tool": "scheduler",
            "message": "job completed",
            "trace_id": "trace-1",
            "run_id": "run-1",
            "job_id": "job-1",
            "duration_ms": 12,
        }
    )

    assert event["event_type"] == "SCHEDULER_JOB_SUCCESS"
    assert event["tool"] == "scheduler"
    assert event["message"] == "job completed"
    assert event["trace_id"] == "trace-1"
    assert event["run_id"] == "run-1"
    assert event["metadata"]["job_id"] == "job-1"
    assert event["metadata"]["duration_ms"] == 12


def test_event_bus_publish_async_runs_async_handlers():
    async def scenario():
        bus = EventBus()
        seen = []

        async def handler(payload):
            await asyncio.sleep(0)
            seen.append(payload["value"])

        bus.subscribe("ASYNC_EVENT", handler)
        await bus.publish_async("ASYNC_EVENT", {"value": "ok"})

        assert seen == ["ok"]

    asyncio.run(scenario())


def test_baton_type_contracts_match_native_schema_fields():
    assert MISSION_BATON_FIELDS == (
        "objective",
        "run_id",
        "history_json",
        "metadata_json",
    )
    assert WORKER_BATON_FIELDS == (
        "objective",
        "status",
        "stage",
        "worker_stdout",
        "error",
        "payload",
    )

    mission = MissionBatonPayload.from_state(
        "contract test",
        {
            "run_id": "run-contract",
            "history": [{"role": "user", "content": "hi"}],
            "metadata": {"trace_id": "tr-contract"},
        },
    )
    native_args = mission.to_native_args()
    assert native_args[0] == "contract test"
    assert native_args[1] == "run-contract"

    restored = MissionBatonPayload.from_native_dict(
        {
            "objective": native_args[0],
            "run_id": native_args[1],
            "history_json": native_args[2],
            "metadata_json": native_args[3],
        }
    )
    assert restored.to_state() == mission.to_state()

    worker = WorkerBatonPayload({"objective": "worker contract", "status": "pending"})
    assert WorkerBatonPayload.from_json(worker.to_json()).data == worker.data


def test_baton_memory_cache_is_bounded():
    manager = BatonManager()
    baton_dir = Path("libs/aja-core/temp_batons") / f"cache-test-{uuid.uuid4().hex}"
    baton_dir.mkdir(parents=True, exist_ok=True)
    manager.baton_dir = baton_dir
    manager.clear_memory_cache()

    try:
        for index in range(_MAX_IN_MEMORY_BATONS + 3):
            manager.capture(
                f"cache bound test {index}",
                {
                    "run_id": f"run-{index}",
                    "history": [],
                    "metadata": {"index": index},
                },
            )

        assert len(_IN_MEMORY_BATONS) == _MAX_IN_MEMORY_BATONS
    finally:
        manager.clear_memory_cache()
        shutil.rmtree(baton_dir, ignore_errors=True)


def test_session_manager_has_explicit_lifecycle_controls():
    manager = SessionManager()

    session = manager.get_or_create("user-1")
    session.log_interaction("user", "hello")
    session.interrupt()

    snapshot = manager.snapshot()
    assert snapshot["user-1"]["history_count"] == 1
    assert snapshot["user-1"]["is_interrupted"] is True

    assert manager.get("user-1") is session
    assert manager.remove("user-1") is True
    assert manager.get("user-1") is None

    manager.get_or_create("user-2")
    manager.reset()
    assert manager.sessions == {}
