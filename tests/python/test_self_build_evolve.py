import pytest
from aja.self_build.capability_builder import self_build_cycle
from aja.self_evolve.reflection import knowledge_base, process_execution
from aja.self_evolve.task_generator import curriculum_manager
from aja.goals.goal_engine import GoalEngine

def test_self_build_and_evolve_modules():
    """Test that self_build and self_evolve load natively without ModuleNotFoundError."""
    res = self_build_cycle("Test capability gap")
    assert res["status"] == "ready"

    entry = process_execution("Test objective", {}, {"success": True})
    assert entry["success"] is True
    assert len(knowledge_base) > 0

    next_goal = curriculum_manager.generate_next_goal()
    assert "benchmark level 1" in next_goal

def test_goal_engine_loads_knowledge_base():
    """Test GoalEngine loads reflection knowledge_base into Planner without error."""
    ge = GoalEngine()
    assert ge.planner is not None
