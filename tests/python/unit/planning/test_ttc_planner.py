"""
=============================================================================
AJA Cognitive Architecture: System-2 TTC Planner & State Tree Unit Tests
=============================================================================
"""

import pytest
from aja.cognitive.state_tree import StateTree
from aja.cognitive.ttc_planner import CandidateBranch, TTCPlanner


def test_state_tree_branching_and_backtracking():
    """Verify tree node creation, observation recording, and parent backtracking."""
    tree = StateTree(root_goal="Deploy API Service")
    assert tree.active_node_id == "root"

    # Create child branch node
    b1 = tree.create_child_node(
        parent_id="root",
        action_type="branch",
        action_payload="Branch 1: Docker Deploy",
        checkpoint_state={"port": 8000},
    )
    assert tree.active_node_id == b1.node_id

    # Create step node under b1
    s1 = tree.create_child_node(
        parent_id=b1.node_id,
        action_type="shell",
        action_payload="docker-compose up -d",
    )
    assert s1.parent_id == b1.node_id

    # Simulate step failure and backtrack
    tree.record_observation(s1.node_id, "Port 8000 already in use", success=False)
    parent = tree.backtrack_to_parent(s1.node_id)
    assert parent.node_id == b1.node_id
    assert s1.status == "rolled_back"


def test_ttc_branch_selection_utility():
    """Verify optimal candidate branch selection based on risk and success probability."""
    planner = TTCPlanner()
    branches = planner.generate_candidate_branches("Refactor auth module and run test suite")
    assert len(branches) >= 3

    optimal = planner.select_optimal_branch(branches)
    assert optimal is not None
    assert optimal.estimated_success_prob >= 0.8
    assert optimal.risk_score <= 0.5


@pytest.mark.anyio
async def test_ttc_execution_recovers_via_backtracking():
    """Verify that if the first branch fails, the planner automatically backtracks and succeeds on the next branch."""
    planner = TTCPlanner()
    call_count = 0

    async def mock_executor(step):
        nonlocal call_count
        call_count += 1
        # Fail on the very first branch execution
        if call_count == 1:
            raise RuntimeError("Diagnostic probe failed: Connection refused")
        return f"Executed step: {step['action']}"

    result = await planner.execute_with_tree_search(
        goal="Configure reverse proxy",
        executor_fn=mock_executor,
    )
    assert result["success"] is True
    assert result["steps_executed"] >= 1
    assert len(result["state_tree_path"]) >= 2
