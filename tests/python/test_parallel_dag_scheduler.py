import pytest
import anyio
from aja.goals.goal_engine import Goal, GoalEngine
from aja.planning.models import PlanGraph, PlanNode

@pytest.mark.anyio
async def test_parallel_dag_node_execution(monkeypatch):
    """Test that independent nodes in DAG execute in parallel batch."""
    ge = GoalEngine()
    ge.goals.clear()
    monkeypatch.setattr(ge, "sync_external_missions", lambda: None)
    monkeypatch.setattr("aja.goals.goal_engine.verify_plan", lambda plan, state=None: {"valid": True})
    monkeypatch.setattr("aja.goals.goal_engine.critique_plan", lambda plan, state: {"issues": [], "severity": 0})
    monkeypatch.setattr("aja.goals.goal_engine.critic_score", lambda plan, critique: 1.0)

    executed_nodes = []

    # Patch the whole SwarmEngine class: constructing the real engine per node
    # pulls heavy dependencies and makes this test flaky under full-suite load.
    from unittest.mock import MagicMock, AsyncMock

    async def fake_execute(task_str, *args, **kwargs):
        executed_nodes.append(task_str)

    mock_engine_instance = MagicMock()
    mock_engine_instance.execute_direct = AsyncMock(side_effect=fake_execute)
    monkeypatch.setattr(
        "aja.orchestration.swarm.SwarmEngine",
        MagicMock(return_value=mock_engine_instance),
    )

    # Nodes 1 and 2 have no dependencies (independent). Node 3 depends on 1 and 2.
    n1 = PlanNode(id="n1", task="Task 1")
    n2 = PlanNode(id="n2", task="Task 2")
    n3 = PlanNode(id="n3", task="Task 3", dependencies=["n1", "n2"])
    plan = PlanGraph(goal="Parallel Task", nodes=[n1, n2, n3])

    gid = ge.add_goal("Parallel Task", priority=1)
    goal = [g for g in ge.goals if g.id == gid][0]
    goal.plan = plan

    # Move status to EXECUTING
    goal.status = "EXECUTING"

    # Step 1: Should execute Task 1 and Task 2 concurrently
    await ge.run_step()
    assert "n1" in goal.progress["completed_steps"]
    assert "n2" in goal.progress["completed_steps"]

    # Step 2: Should execute Task 3 (now that n1 and n2 are completed)
    await ge.run_step()
    assert "n3" in goal.progress["completed_steps"]
