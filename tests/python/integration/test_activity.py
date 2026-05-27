import asyncio
import pytest

from aja.runtime.execution.activity import durable_activity, ActivityContext, set_activity_context
from aja.runtime.execution.sequencer import TelemetryEmitter, EventSequencer
from pathlib import Path

@durable_activity("mock_network_request")
async def mock_network_request(data: str) -> str:
    # Simulate network latency
    await asyncio.sleep(0.01)
    return f"processed: {data}"

@durable_activity("failing_request")
def failing_request(data: str) -> str:
    raise ValueError(f"failed on: {data}")

def test_durable_activity_live_and_replay(tmp_path: Path):
    session_id = "test-session-123"
    root = tmp_path / session_id
    sequencer = EventSequencer(session_id, "test-trace")
    emitter = TelemetryEmitter(root, sequencer)
    
    # 1. Live Run
    ctx = ActivityContext(is_replay=False, emitter=emitter)
    set_activity_context(ctx)
    
    async def run_live():
        result = await mock_network_request("hello")
        assert result == "processed: hello"
        
        with pytest.raises(ValueError, match="failed on: bad"):
            failing_request("bad")
            
    asyncio.run(run_live())
    
    # Verify events were written to the timeline
    assert emitter.timeline_path.exists()
    
    events = []
    for line in emitter.timeline_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FRAME:"):
            import json
            parts = line.split(":", 3)
            events.append(json.loads(parts[3]))
            
    assert len(events) == 2
    assert events[0]["event_type"] == "EXECUTION_ACTIVITY"
    assert events[0]["activity_name"] == "mock_network_request"
    assert events[0]["result"] == "processed: hello"
    
    assert events[1]["event_type"] == "EXECUTION_ACTIVITY"
    assert events[1]["activity_name"] == "failing_request"
    assert events[1]["status"] == "error"
    
    # 2. Replay Run
    # Provide the recorded events to the replay context
    replay_ctx = ActivityContext(is_replay=True, replay_events=events)
    set_activity_context(replay_ctx)
    
    async def run_replay():
        # This should return the recorded result without sleeping
        # We can test it by passing different args to verify it returns the recorded result,
        # but the decorator doesn't currently check if the args match perfectly, it just yields the next sequence.
        result = await mock_network_request("ignored")
        assert result == "processed: hello"
        
        with pytest.raises(RuntimeError, match="Recorded activity 'failing_request' failed: failed on: bad"):
            failing_request("ignored")
            
    asyncio.run(run_replay())
