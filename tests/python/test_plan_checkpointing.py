import pytest
from aja.goals.goal_engine import Goal
from aja.planning.models import PlanGraph, PlanNode

def test_goal_plan_serialization():
    """Test that Goal cleanly serializes and restores its PlanGraph and node outputs."""
    n1 = PlanNode(id="n1", task="step 1")
    n2 = PlanNode(id="n2", task="step 2", dependencies=["n1"])
    plan = PlanGraph(goal="Build Feature", nodes=[n1, n2])

    goal = Goal(objective="Build Feature", priority=1)
    goal.plan = plan
    goal.current_node_index = 1
    goal.progress["completed_steps"] = ["n1"]
    goal.progress["node_outputs"] = {"n1": {"success": True, "output": "Artifact created"}}

    # Serialize
    d = goal.to_dict()
    assert "plan" in d
    assert d["plan"]["goal"] == "Build Feature"
    assert len(d["plan"]["nodes"]) == 2

    # Deserialize
    restored_goal = Goal.from_dict(d)
    assert restored_goal.id == goal.id
    assert restored_goal.current_node_index == 1
    assert restored_goal.plan is not None
    assert restored_goal.plan.goal == "Build Feature"
    assert len(restored_goal.plan.nodes) == 2
    assert restored_goal.plan.nodes[0].id == "n1"
    assert restored_goal.progress["node_outputs"]["n1"]["output"] == "Artifact created"
