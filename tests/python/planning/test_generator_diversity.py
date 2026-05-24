import pytest
from unittest.mock import MagicMock, patch
from aja.planning.models import PlanGraph, PlanNode, DoD
from aja.planning.generator import generate_candidate_plans

def _make_dummy_graph(goal: str, tasks: list[str]) -> PlanGraph:
    nodes = []
    for idx, t in enumerate(tasks):
        nodes.append(
            PlanNode(
                id=f"P{idx+1}",
                task=t,
                dependencies=[],
                strategy="direct",
                inputs=[],
                outputs={},
                dod=DoD("done", "deterministic"),
                uncertainty=0.1
            )
        )
    return PlanGraph(goal=goal, nodes=nodes)

def test_generate_candidate_plans_diversity():
    """
    Verify that generate_candidate_plans increases temperature and adds previously
    generated plans to history when duplicates are generated.
    """
    goal = "Test Goal"
    state = {"retrieved_context": "dummy context"}
    k = 2

    # We will simulate the LLM returning the same duplicate plan initially,
    # and then finally returning a diverse plan.
    duplicate_raw = "{\"goal\": \"Test Goal\", \"nodes\": [{\"id\": \"P1\", \"task\": \"Task A\", \"type\": \"primitive\", \"dod\": {\"success_criteria\": \"done\", \"validation_type\": \"deterministic\"}}]}"
    diverse_raw = "{\"goal\": \"Test Goal\", \"nodes\": [{\"id\": \"P1\", \"task\": \"Task B\", \"type\": \"primitive\", \"dod\": {\"success_criteria\": \"done\", \"validation_type\": \"deterministic\"}}]}"
    
    # We patch retrieve_methods to return nothing, so it forces LLM generation
    with patch("aja.planning.generator.retrieve_methods", return_value=[]), \
         patch("aja.planning.planner.Planner._call_llm") as mock_call_llm:
        
        # 1st call: returns duplicate_raw
        # 2nd call: returns duplicate_raw (triggers similar to existing warning)
        # 3rd call: returns diverse_raw (gets accepted as diverse)
        mock_call_llm.side_effect = [duplicate_raw, duplicate_raw, diverse_raw]

        plans = generate_candidate_plans(goal, state, k=k)

        # We should have successfully generated 2 diverse plans: one with Task A, and one with Task B
        assert len(plans) == 2
        assert plans[0].nodes[0].task == "Task A"
        assert plans[1].nodes[0].task == "Task B"

        # Check call arguments to verify history propagation and temperature increases
        assert mock_call_llm.call_count == 3
        
        # 1st call: initial attempt
        first_call_args = mock_call_llm.call_args_list[0]
        assert first_call_args[1]["config"]["temperature"] == pytest.approx(0.3)
        assert first_call_args[1]["history"] == []

        # 2nd call: attempts = 1 (1 duplicate generated previously)
        second_call_args = mock_call_llm.call_args_list[1]
        assert second_call_args[1]["config"]["temperature"] == pytest.approx(0.45)
        # It must contain the summary of the first generated plan
        assert len(second_call_args[1]["history"]) == 1
        assert "Task A" in second_call_args[1]["history"][0]

        # 3rd call: attempts = 2
        third_call_args = mock_call_llm.call_args_list[2]
        assert third_call_args[1]["config"]["temperature"] == pytest.approx(0.6)
        assert len(third_call_args[1]["history"]) == 1
        assert "Task A" in third_call_args[1]["history"][0]
