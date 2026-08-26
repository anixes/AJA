import pytest
from aja.goals.goal_engine import GoalEngine, Goal
from aja.planning.models import PlanGraph, PlanNode

pytestmark = pytest.mark.anyio


@pytest.mark.anyio
async def test_goal_progresses_through_fsm_states(monkeypatch):
    """Test that a goal moves PENDING -> PLANNING -> EXECUTING -> VERIFYING -> DONE."""
    ge = GoalEngine()
    ge.goals.clear()
    monkeypatch.setattr(ge, "sync_external_missions", lambda: None)

    # Mock LLM verification / critique functions to keep unit test sub-millisecond fast
    monkeypatch.setattr("aja.goals.goal_engine.verify_plan", lambda plan, state=None: {"valid": True})
    monkeypatch.setattr("aja.goals.goal_engine.critique_plan", lambda plan, state: {"issues": [], "severity": 0})
    monkeypatch.setattr("aja.goals.goal_engine.critic_score", lambda plan, critique: 1.0)
    monkeypatch.setattr("aja.learning.strategy_store.process_strategy_learning", lambda *a, **k: None)

    n1 = PlanNode(id="n1", task="echo step 1")
    n2 = PlanNode(id="n2", task="echo step 2", dependencies=["n1"])
    plan = PlanGraph(goal="Test multi-node task", nodes=[n1, n2])

    async def _async_expand(g):
        return plan

    monkeypatch.setattr(ge, "expand_goal", lambda g: plan)
    monkeypatch.setattr(ge, "expand_goal_async", _async_expand)

    # Add goal
    gid = ge.add_goal("Test multi-node task", priority=1)
    ge.goals = [g for g in ge.goals if g.id == gid]  # Keep only this test goal
    goal = ge.goals[0]

    assert goal.status == "PENDING"

    # Tick 1: PENDING -> PLANNING
    await ge.run_step()
    assert goal.status == "PLANNING"
    assert len(goal.plan.nodes) == 2

    # Tick 2: PLANNING -> EXECUTING
    await ge.run_step()
    assert goal.status == "EXECUTING"
    assert goal.current_node_index == 0

    # Mock execute_direct method signature (self, task_str, ...)
    async def mock_execute_direct(self, task_str, *args, **kwargs):
        return True

    monkeypatch.setattr("aja.orchestration.swarm.SwarmEngine.execute_direct", mock_execute_direct)

    # Tick 3: Execute Node 1
    await ge.run_step()
    assert goal.current_node_index == 1
    assert "n1" in goal.progress["completed_steps"]

    # Tick 4: Execute Node 2
    await ge.run_step()
    assert goal.current_node_index == 2
    assert "n2" in goal.progress["completed_steps"]

    # Tick 5: EXECUTING -> VERIFYING
    await ge.run_step()
    assert goal.status == "VERIFYING"

    # Tick 6: VERIFYING -> DONE
    await ge.run_step()
    assert goal.status == "DONE"


@pytest.mark.anyio
async def test_node_failure_retry_and_replan(monkeypatch):
    """Test that a node failure retries max_retries then triggers replan."""
    ge = GoalEngine()
    ge.goals.clear()
    monkeypatch.setattr(ge, "sync_external_missions", lambda: None)

    # Mock LLM verification / critique functions
    monkeypatch.setattr("aja.goals.goal_engine.verify_plan", lambda plan, state=None: {"valid": True})
    monkeypatch.setattr("aja.goals.goal_engine.critique_plan", lambda plan, state: {"issues": [], "severity": 0})
    monkeypatch.setattr("aja.goals.goal_engine.critic_score", lambda plan, critique: 1.0)
    monkeypatch.setattr("aja.learning.strategy_store.process_strategy_learning", lambda *a, **k: None)

    n1 = PlanNode(id="n1", task="failing step")
    plan = PlanGraph(goal="Test failing node", nodes=[n1])

    monkeypatch.setattr(ge, "expand_goal", lambda g: plan)

    async def failing_execute_direct(self, task_str, *args, **kwargs):
        raise RuntimeError("Simulated command failure")

    monkeypatch.setattr("aja.orchestration.swarm.SwarmEngine.execute_direct", failing_execute_direct)

    gid = ge.add_goal("Test failing node", priority=1)
    ge.goals = [g for g in ge.goals if g.id == gid]  # Keep only this test goal
    goal = ge.goals[0]

    # PENDING -> PLANNING -> EXECUTING
    await ge.run_step()
    await ge.run_step()
    assert goal.status == "EXECUTING"

    # Retries 1, 2, 3
    for attempt in range(1, ge.max_retries + 1):
        await ge.run_step()
        assert goal.retries == attempt

    # Next attempt exceeds max_retries -> triggers re-plan -> status back to PENDING
    await ge.run_step()
    assert goal.replan_count == 1
    assert goal.status == "PENDING"
