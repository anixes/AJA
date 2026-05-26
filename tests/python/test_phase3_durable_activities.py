import pytest
import os
from unittest.mock import MagicMock, patch
from pathlib import Path
from aja.runtime.execution.activity import ActivityContext, set_activity_context, get_activity_context
from aja.runtime.execution import get_default_execution_manager, ExecutionRequest, ExecutionResult
from aja.orchestration.gateway import LLMGateway
from aja.runtime.handover import BatonManager

@pytest.mark.anyio
async def test_subprocess_dispatch_durable_replay(tmp_path):
    manager = get_default_execution_manager()
    # Mock self._emitter to a local TelemetryEmitter
    from aja.runtime.execution.sequencer import TelemetryEmitter, EventSequencer
    direct_root = tmp_path / "executions" / "direct"
    sequencer = EventSequencer("direct")
    emitter = TelemetryEmitter(direct_root, sequencer)
    
    # 1. Live Run: run a simple echo command
    ctx = ActivityContext(is_replay=False, emitter=emitter)
    set_activity_context(ctx)
    
    req = ExecutionRequest(command="echo 'durable execution works'", timeout=10.0, workspace_mode="direct")
    result = await manager.run(req)
    assert result.success is True
    assert "durable execution works" in result.stdout.strip()
    
    # Verify the activity was recorded
    assert emitter.timeline_path.exists()
    events = []
    import json
    for line in emitter.timeline_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FRAME:"):
            parts = line.split(":", 3)
            events.append(json.loads(parts[3]))
            
    act_events = [e for e in events if e.get("event_type") == "EXECUTION_ACTIVITY"]
    assert len(act_events) >= 1
    assert act_events[0]["activity_name"] == "subprocess.dispatch"
    
    # 2. Replay Run: run the same command, but mock start/wait to verify they are bypassed
    replay_ctx = ActivityContext(is_replay=True, replay_events=events)
    set_activity_context(replay_ctx)
    
    # If we call run() during replay, it should intercept and return the identical recorded ExecutionResult
    # without spawning any subprocesses
    with patch.object(manager, "start") as mock_start:
        replay_result = await manager.run(req)
        mock_start.assert_not_called()
        assert replay_result.success == result.success
        assert replay_result.stdout == result.stdout

@pytest.mark.anyio
async def test_llm_gateway_durable_replay(tmp_path):
    # Setup default TelemetryEmitter
    from aja.runtime.execution.sequencer import TelemetryEmitter, EventSequencer
    direct_root = tmp_path / "executions" / "direct"
    sequencer = EventSequencer("direct")
    emitter = TelemetryEmitter(direct_root, sequencer)
    
    # Mock real network call for LLMGateway
    gateway = LLMGateway(provider="openai", api_key="test-key", base_url="http://localhost:8080/v1")
    
    # 1. Live Run: call chat under a mocked OpenAI Client response
    ctx = ActivityContext(is_replay=False, emitter=emitter)
    set_activity_context(ctx)
    
    with patch("aja.orchestration.gateway.AsyncOpenAI") as mock_openai:
        # Setup mock completion choice
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "mocked llm output"
        
        # Async mock client context manager
        mock_client = MagicMock()
        from unittest.mock import AsyncMock
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        
        mock_openai.return_value.__aenter__.return_value = mock_client
        
        response = await gateway.chat(model="gpt-4", prompt="What is AJA?")
        assert response == "mocked llm output"
        
    # Verify the activity was recorded
    events = []
    import json
    for line in emitter.timeline_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FRAME:"):
            parts = line.split(":", 3)
            events.append(json.loads(parts[3]))
            
    act_events = [e for e in events if e.get("event_type") == "EXECUTION_ACTIVITY"]
    assert len(act_events) >= 1
    assert act_events[0]["activity_name"] == "llm.chat"
    
    # 2. Replay Run: verify chat doesn't initialize AsyncOpenAI and yields recorded output
    replay_ctx = ActivityContext(is_replay=True, replay_events=events)
    set_activity_context(replay_ctx)
    
    with patch("aja.orchestration.gateway.AsyncOpenAI") as mock_openai_replay:
        replay_response = await gateway.chat(model="gpt-4", prompt="What is AJA?")
        mock_openai_replay.assert_not_called()
        assert replay_response == "mocked llm output"

def test_baton_handover_durable_replay(tmp_path):
    from aja.runtime.execution.sequencer import TelemetryEmitter, EventSequencer
    direct_root = tmp_path / "executions" / "direct"
    sequencer = EventSequencer("direct")
    emitter = TelemetryEmitter(direct_root, sequencer)
    
    baton_manager = BatonManager()
    
    # Mock aja_native.write_baton to avoid C-level errors in tests
    with patch("aja.runtime.handover.aja_native") as mock_native:
        # 1. Live Run: capture and pickup
        ctx = ActivityContext(is_replay=False, emitter=emitter)
        set_activity_context(ctx)
        
        orchestrator_state = {"objective": "Task objectives", "run_id": "run-abc", "history": [], "metadata": {}}
        code = baton_manager.capture("Perform scan", orchestrator_state)
        assert len(code) == 6
        
        state = baton_manager.pickup(code)
        assert state is not None
        assert state["objective"] == "Perform scan"
        
    # Verify both capture and pickup were recorded
    events = []
    import json
    for line in emitter.timeline_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FRAME:"):
            parts = line.split(":", 3)
            events.append(json.loads(parts[3]))
            
    act_events = [e for e in events if e.get("event_type") == "EXECUTION_ACTIVITY"]
    assert len(act_events) == 2
    assert act_events[0]["activity_name"] == "baton.capture"
    assert act_events[1]["activity_name"] == "baton.pickup"
    
    # 2. Replay Run: mock pickup to verify Arrow deserialization is fully bypassed
    replay_ctx = ActivityContext(is_replay=True, replay_events=events)
    set_activity_context(replay_ctx)
    
    with patch("aja.runtime.handover._get_cached_baton") as mock_cache:
        replay_code = baton_manager.capture("Perform scan", orchestrator_state)
        assert replay_code == code  # Intercepted!
        
        replay_state = baton_manager.pickup(code)
        mock_cache.assert_not_called()
        assert replay_state["objective"] == "Perform scan"
