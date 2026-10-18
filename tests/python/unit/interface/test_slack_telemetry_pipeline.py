"""Night-shift wave-3: Slack telemetry pipeline regression.

Slack missions previously promised live reports that never arrived: the
adapter had per-channel tail queues but nothing fed them (no bus
subscription, no dispatcher). This pins the full pipeline:
bus.publish -> handler -> shared queue -> dispatcher fan-out -> channel queue.
"""

import asyncio

import pytest

from aja.gateway.adapters.slack_adapter import SlackAdapter
from aja.runtime.event_bus import bus, EVENTS


@pytest.fixture()
def adapter():
    a = SlackAdapter({"token": "x-test-token"})
    a.is_running = True
    yield a
    # Cleanup: unsubscribe handlers + cancel tasks even on failure
    for event_name, handler in a._bus_handlers:
        try:
            bus.unsubscribe(event_name, handler)
        except Exception:
            pass
    a._bus_handlers.clear()
    for task in list(a._tail_tasks.values()):
        task.cancel()
    if a._dispatcher_task and not a._dispatcher_task.done():
        a._dispatcher_task.cancel()


def test_bus_subscription_registers_all_event_handlers(adapter):
    adapter._subscribe_bus_events()
    names = {name for name, _ in adapter._bus_handlers}
    assert names == set(EVENTS.values())


def test_handler_queues_event_into_shared_queue(adapter):
    handler = adapter._make_event_handler(EVENTS["TASK_RECEIVED"])
    handler({"message": "mission started", "mission_id": "m-1"})
    ev = adapter.telemetry_queue.get_nowait()
    assert ev["kind"] == "TASK_RECEIVED"
    assert ev["message"] == "mission started"
    assert ev["target"] == "m-1"


def test_failed_events_are_marked_error(adapter):
    handler = adapter._make_event_handler("MISSION_FAILED")
    handler({"message": "boom"})
    ev = adapter.telemetry_queue.get_nowait()
    assert ev["status"] == "ERROR"


def test_approval_events_survive_full_queue(adapter):
    adapter.telemetry_queue = asyncio.Queue(maxsize=1)
    adapter._put_telemetry({"kind": "MISSION_CREATED", "message": "filler", "event_id": "old"})
    adapter._put_telemetry({"kind": "AWAITING_APPROVAL", "message": "need ok", "event_id": "appr"})
    kinds = []
    while not adapter.telemetry_queue.empty():
        kinds.append(adapter.telemetry_queue.get_nowait()["kind"])
    assert "AWAITING_APPROVAL" in kinds


def test_dispatcher_fans_out_to_all_subscribed_channels(adapter):
    received_a, received_b = asyncio.Queue(), asyncio.Queue()
    adapter._chat_queues["chan-a"] = received_a
    adapter._chat_queues["chan-b"] = received_b
    handler = adapter._make_event_handler(EVENTS["MISSION_COMPLETED"])

    async def scenario():
        adapter._dispatcher_task = asyncio.create_task(adapter._dispatch_telemetry())
        handler({"message": "finished", "mission_id": "m-9"})
        await asyncio.wait_for(received_a.get(), timeout=5)
        await asyncio.wait_for(received_b.get(), timeout=5)
        adapter._dispatcher_task.cancel()

    asyncio.run(scenario())


def test_tail_forwards_queue_events_to_slack_send(adapter, monkeypatch):
    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append((chat_id, text))

    monkeypatch.setattr(adapter, "send_message", fake_send)
    adapter.is_running = True

    async def scenario():
        adapter._chat_queues["chan-x"] = asyncio.Queue()
        adapter._tail_tasks["chan-x"] = asyncio.create_task(adapter.tail_events("chan-x"))
        adapter._chat_queues["chan-x"].put_nowait(
            {"status": "SUCCESS", "message": "it works"}
        )
        for _ in range(50):
            if sent:
                break
            await asyncio.sleep(0.05)
        adapter._tail_tasks["chan-x"].cancel()

    asyncio.run(scenario())
    assert sent and sent[0][0] == "chan-x" and "it works" in sent[0][1]


