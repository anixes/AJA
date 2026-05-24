import pytest
from fastapi.testclient import TestClient
from aja.server.mobile_bridge import app
from aja.runtime.event_bus import bus, EVENTS

def test_websocket_event_broadcasting():
    client = TestClient(app)
    
    # 1. Connect to websocket
    with client.websocket_connect("/ws/mobile") as websocket:
        # 2. Publish an event to the EventBus
        test_payload = {"task_id": "test-123", "objective": "Write clean code"}
        bus.publish(EVENTS["TASK_RECEIVED"], test_payload)
        
        # 3. Read messages in a loop until we find the event_broadcast to handle concurrent state_updates
        found = False
        for _ in range(5):
            msg = websocket.receive_json()
            if msg["type"] == "event_broadcast":
                assert msg["event_type"] == EVENTS["TASK_RECEIVED"]
                assert msg["data"] == test_payload
                found = True
                break
        
        assert found, "Event broadcast not received on websocket"
