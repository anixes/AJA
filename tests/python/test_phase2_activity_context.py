import pytest
import os
from unittest.mock import MagicMock
from aja.runtime.execution.activity import ActivityContext, get_activity_context
from aja.planning.react_executor import ReActExecutor
from aja.planning.models import PlanGraph, PlanNode
from aja.orchestration.swarm import SwarmEngine

def test_activity_id_for_monotonicity_and_frames():
    ctx = ActivityContext(is_replay=False)
    id1 = ctx.activity_id_for("test_act")
    id2 = ctx.activity_id_for("test_act")
    assert id1 != id2
    assert "test_act" in id1
    assert "test_act" in id2

    # Using explicit sequence should bypass frame tracking
    id3 = ctx.activity_id_for("test_act", sequence=42)
    assert id3 == "test_act_42"

@pytest.mark.anyio
async def test_activity_context_in_direct_execution():
    engine = SwarmEngine(model="gpt-4", presenter=MagicMock())
    engine.dry_run = True
    
    # Mock chat to return empty/simulated prompt to exit quickly
    engine.gateway = MagicMock()
    async def mock_chat(*args, **kwargs):
        return ""
    engine.gateway.chat = mock_chat
    
    # Run execute_direct and verify it doesn't crash
    await engine.execute_direct("Perform basic check")

def test_activity_context_isolation_react_executor():
    # Setup minimal PlanGraph and PlanNode
    node = PlanNode(id="node_1", tool="terminal.exec", task="test task")
    graph = PlanGraph(goal="test_goal", nodes=[node])
    
    executor = ReActExecutor(graph=graph)
    executor._bridge = MagicMock()
    
    # Mock _bridge.run_node to verify that a context is active when called
    context_active_during_run = False
    def mock_run_node(n):
        nonlocal context_active_during_run
        ctx = get_activity_context()
        if ctx is not None:
            context_active_during_run = True
        return True
    
    executor._bridge.run_node = mock_run_node
    
    # Let's run executor
    executor.run()
    
    assert context_active_during_run, "ActivityContext should have been active during run_node"
    assert get_activity_context() is None, "ActivityContext should be reset after execution"
