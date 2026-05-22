import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from agentx.goals.goal_engine import GoalEngine, Goal
from agentx.gateway.tg_client import TelegramAdapter
from agentx.gateway.orchestrator import UnifiedGateway
from agentx.runtime.event_bus import EVENTS

def test_safe_read_only_command_detection():
    engine = GoalEngine()
    # Safe commands
    assert engine._is_safe_read_only("dir") is True
    assert engine._is_safe_read_only("ls -la") is True
    assert engine._is_safe_read_only("cat file.txt") is True
    assert engine._is_safe_read_only("type file.py") is True
    assert engine._is_safe_read_only("/run dir") is True
    assert engine._is_safe_read_only("/ls") is True
    assert engine._is_safe_read_only("echo 'hello'") is True
    assert engine._is_safe_read_only("pwd") is True
    assert engine._is_safe_read_only("whoami") is True

    # Unsafe commands / chaining / injection
    assert engine._is_safe_read_only("rm -rf /") is False
    assert engine._is_safe_read_only("dir; rm -rf") is False
    assert engine._is_safe_read_only("ls && cat file") is False
    assert engine._is_safe_read_only("cat file | grep text") is False
    assert engine._is_safe_read_only("echo `whoami`") is False
    assert engine._is_safe_read_only("type file.txt > output.txt") is False

def test_complexity_aware_bypass():
    engine = GoalEngine()
    goal = Goal("dir", 1)
    
    with patch('agentx.planning.scorer.estimate_complexity') as mock_estimate:
        mock_estimate.return_value = "LOW"
        plan = engine.expand_goal(goal)
        assert plan is not None
        # Should return _fallback_graph plan nodes
        assert hasattr(plan, "nodes")
        assert len(plan.nodes) > 0

@pytest.mark.anyio
async def test_telegram_telemetry_lancedb_events_bypass_dedup():
    adapter = TelegramAdapter({"token": "test-token"})
    adapter.is_running = True
    
    # Mock send_message and send_notification
    adapter.send_message = AsyncMock()
    adapter.send_notification = AsyncMock()
    
    # Queue MISSION_RESULT event
    await adapter.telemetry_queue.put({
        "event_id": "e1",
        "kind": "MISSION_RESULT",
        "target": "m1",
        "status": "SUCCESS",
        "message": "Results of dir",
        "command": "dir",
        "timestamp": "2026-05-21T12:00:00Z"
    })
    
    # Queue PLAN_CREATED event
    await adapter.telemetry_queue.put({
        "event_id": "e2",
        "kind": "PLAN_CREATED",
        "target": "m1",
        "status": "SUCCESS",
        "message": "Plan: node A -> B",
        "command": "",
        "timestamp": "2026-05-21T12:00:01Z"
    })
    
    # Run a short tail_events cycle
    tail_task = asyncio.create_task(adapter.tail_events("123"))
    await asyncio.sleep(0.1)
    
    # Stop and await tail
    adapter.is_running = False
    await adapter.telemetry_queue.put({
        "event_id": "stop",
        "kind": "STOP",
        "target": "stop",
        "status": "INFO",
        "message": "stop",
        "command": "",
        "timestamp": ""
    })
    try:
        await asyncio.wait_for(tail_task, timeout=1.0)
    except Exception:
        pass
        
    # Check that send_notification was called with "normal" importance for the non-deduped kinds
    assert adapter.send_notification.call_count >= 2
    
    calls = adapter.send_notification.call_args_list
    kinds_found = []
    for call in calls:
        args, kwargs = call
        if "Results of dir" in args[1]:
            assert kwargs.get("importance") == "normal"
            kinds_found.append("MISSION_RESULT")
        elif "Plan: node A -> B" in args[1]:
            assert kwargs.get("importance") == "normal"
            kinds_found.append("PLAN_CREATED")
            
    assert "MISSION_RESULT" in kinds_found
    assert "PLAN_CREATED" in kinds_found

@pytest.mark.anyio
async def test_intent_router_safe_verbs():
    gateway = UnifiedGateway()
    # Test deterministic verbs
    assert await gateway.route_intent("dir") == "MISSION"
    assert await gateway.route_intent("ls") == "MISSION"
    assert await gateway.route_intent("open file.txt") == "MISSION"
    assert await gateway.route_intent("list files") == "MISSION"
    assert await gateway.route_intent("read main.py") == "MISSION"

@pytest.mark.anyio
async def test_llm_gateway_conversation_memory():
    from agentx.orchestration.gateway import LLMGateway
    
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "how are you?"}
    ]
    
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="mock-response"))]
    )
    
    mock_async_context_manager = MagicMock()
    mock_async_context_manager.__aenter__ = AsyncMock(return_value=mock_client)
    mock_async_context_manager.__aexit__ = AsyncMock()
    
    with patch("agentx.orchestration.gateway.AsyncOpenAI", return_value=mock_async_context_manager):
        gw = LLMGateway(provider="openrouter", api_key="test-key")
        response = await gw.chat(model="gpt-4", prompt=messages, system="You are helpful")
        
        assert response == "mock-response"
        called_messages = mock_client.chat.completions.create.call_args[1]["messages"]
        assert called_messages[0] == {"role": "system", "content": "You are helpful"}
        assert called_messages[1] == {"role": "user", "content": "hello"}
        assert called_messages[2] == {"role": "assistant", "content": "hi there"}
        assert called_messages[3] == {"role": "user", "content": "how are you?"}

@pytest.mark.anyio
async def test_unified_gateway_chat_history():
    from agentx.gateway.orchestrator import UnifiedGateway
    gw = UnifiedGateway()
    gw.trajectory_manager = MagicMock()
    gw.trajectory_manager.analyze.return_value = '{"should_compress": false, "compress_start": 0, "compress_end": 0}'
    
    chat_history = [
        {"role": "user", "text": "hello bot"},
        {"role": "assistant", "text": "hello user"}
    ]
    
    with patch('agentx.gateway.orchestrator.completion') as mock_completion:
        mock_completion.return_value = "response text"
        
        response = await gw.chat("how are you?", chat_history=chat_history)
        
        assert response == "response text"
        called_prompt = mock_completion.call_args[1]["prompt"]
        assert len(called_prompt) == 3
        assert called_prompt[0] == {"role": "user", "content": "hello bot"}
        assert called_prompt[1] == {"role": "assistant", "content": "hello user"}
        assert called_prompt[2] == {"role": "user", "content": "how are you?"}

@pytest.mark.anyio
async def test_manual_swarm_override_routing():
    from agentx.gateway.orchestrator import UnifiedGateway
    from agentx.gateway.base import MessageEvent, MessageType
    import json
    
    gw = UnifiedGateway()
    # Mock tg_adapter and aja_memory
    gw.telegram_adapter = AsyncMock()
    gw.telegram_adapter.send_message = AsyncMock()
    
    gw.aja_memory = MagicMock()
    mock_mission = {"mission_id": "M-TEST1", "goal": "dir"}
    gw.aja_memory.create_mission.return_value = mock_mission
    gw.aja_memory.get_active_workers.return_value = []
    
    # Message starting with /swarm
    event1 = MessageEvent(
        platform="telegram",
        chat_id="123",
        user_id="user1",
        text="/swarm dir",
        message_type=MessageType.TEXT,
        raw_event=None
    )
    
    with patch.object(gw, '_is_telegram_user_authorized', return_value=True):
        await gw.handle_gateway_event(event1)
        
        # Verify it created a mission for "dir" (stripped of /swarm)
        gw.aja_memory.create_mission.assert_called_with("dir")
        
        # Verify it updated the metadata with force_swarm: True
        gw.aja_memory.update_mission.assert_called_with(
            "M-TEST1",
            {"metadata_json": json.dumps({"force_swarm": True})}
        )

def test_goal_engine_honors_force_swarm():
    from agentx.goals.goal_engine import GoalEngine, Goal
    from unittest.mock import patch, MagicMock
    
    engine = GoalEngine()
    goal = Goal("dir", 1)
    
    # 1. Without force_swarm, dir is a safe read-only command
    assert engine._is_safe_read_only(goal.objective) is True
    
    # 2. With force_swarm: True inside metadata
    goal.metadata = {"force_swarm": True}
    
    # Check that expand_goal does NOT bypass to low-complexity low-level graph when force_swarm is True
    with patch('agentx.planning.scorer.estimate_complexity') as mock_complexity:
        mock_complexity.return_value = "LOW"
        # We patch planner.decompose so it returns a dummy plan instead of trying to run actual LLM
        mock_plan = MagicMock()
        engine.planner.decompose = MagicMock(return_value=mock_plan)
        
        plan = engine.expand_goal(goal)
        # Should not return the _fallback_graph plan, but instead call decompose
        engine.planner.decompose.assert_called_once()
        assert plan == mock_plan

