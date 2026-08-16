"""
=============================================================================
AJA Cognitive Architecture: System-2 Test-Time Compute (TTC) Planner
=============================================================================
Implements Test-Time Compute scaling:
- Multi-candidate branch generation (Best-of-N rollout exploration)
- Pre-mutation risk & feasibility evaluation
- Dynamic state-tree exploration with automatic backtracking on execution failure
=============================================================================
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from aja.cognitive.state_tree import StateNode, StateTree

logger = logging.getLogger(__name__)


@dataclass
class CandidateBranch:
    branch_id: str
    description: str
    action_type: str  # "codeact_python", "codeact_shell", "tool_call", "multi_step"
    steps: List[Dict[str, Any]]
    risk_score: float = 0.0  # 0.0 to 1.0
    estimated_success_prob: float = 0.9
    simulated_outcome: Optional[str] = None


class TTCPlanner:
    """
    System-2 Test-Time Compute Planner.
    Performs branch generation, pre-execution risk rollout, and dynamic backtracking.
    """

    def __init__(self, default_n_candidates: int = 3):
        self.default_n_candidates = default_n_candidates

    def generate_candidate_branches(self, goal: str, context_summary: str = "") -> List[CandidateBranch]:
        """
        Generates candidate execution paths for a complex mission.
        In production, this synthesizes diverse hypotheses (e.g. surgical patch vs test-first vs diagnostic probe).
        """
        goal_lower = goal.lower()
        branches: List[CandidateBranch] = []

        # 1. Conservative Diagnostic Probe Branch (Lowest risk)
        branches.append(
            CandidateBranch(
                branch_id=f"branch-diag-{uuid.uuid4().hex[:6]}",
                description=f"Inspect ground truth and environment state before mutating for: '{goal}'",
                action_type="codeact_python",
                steps=[
                    {"action": "inspect_state", "payload": "import os, sys, platform; print(f'Target: {os.getcwd()}')"}
                ],
                risk_score=0.1,
                estimated_success_prob=0.95,
            )
        )

        # 2. Direct Solution Branch
        branches.append(
            CandidateBranch(
                branch_id=f"branch-direct-{uuid.uuid4().hex[:6]}",
                description=f"Execute primary implementation actions for: '{goal}'",
                action_type="codeact_shell" if any(kw in goal_lower for kw in ["git", "install", "docker", "service"]) else "codeact_python",
                steps=[
                    {"action": "primary_execution", "payload": f"# Primary action for {goal}"}
                ],
                risk_score=0.3 if "delete" not in goal_lower else 0.8,
                estimated_success_prob=0.85,
            )
        )

        # 3. Test-Driven Verification Branch
        branches.append(
            CandidateBranch(
                branch_id=f"branch-tdd-{uuid.uuid4().hex[:6]}",
                description=f"Execute with verification harness and empirical assertions for: '{goal}'",
                action_type="codeact_python",
                steps=[
                    {"action": "verify_preconditions", "payload": "print('Checking preconditions')"},
                    {"action": "apply_solution", "payload": f"# Apply {goal}"},
                    {"action": "assert_success", "payload": "print('Asserting success criteria')"},
                ],
                risk_score=0.2,
                estimated_success_prob=0.92,
            )
        )

        return branches

    def score_branch(self, branch: CandidateBranch) -> float:
        """
        Calculates branch utility score: Utility = SuccessProb * (1.0 - 0.5 * RiskScore)
        """
        return branch.estimated_success_prob * (1.0 - 0.5 * branch.risk_score)

    def select_optimal_branch(self, branches: List[CandidateBranch]) -> CandidateBranch:
        """Picks the highest scoring candidate branch based on utility function."""
        if not branches:
            raise ValueError("No candidate branches provided for selection.")
        return max(branches, key=self.score_branch)

    async def execute_with_tree_search(
        self,
        goal: str,
        executor_fn: Callable[[Dict[str, Any]], Any],
        context_summary: str = "",
    ) -> Dict[str, Any]:
        """
        Executes a goal using state-tree search and automatic backtracking upon step failures.
        """
        tree = StateTree(root_goal=goal)
        branches = self.generate_candidate_branches(goal, context_summary)
        
        # Sort branches by score descending
        sorted_branches = sorted(branches, key=self.score_branch, reverse=True)
        
        last_error = None
        executed_nodes: List[StateNode] = []

        for branch in sorted_branches:
            logger.info("TTC Exploring branch: %s (Utility: %.2f)", branch.description, self.score_branch(branch))
            
            # Checkpoint at root before branch execution
            branch_node = tree.create_child_node(
                parent_id="root",
                action_type=branch.action_type,
                action_payload=branch.description,
                risk_score=branch.risk_score,
                checkpoint_state={"branch_id": branch.branch_id},
            )
            
            branch_success = True
            step_outputs = []

            for step in branch.steps:
                step_node = tree.create_child_node(
                    parent_id=branch_node.node_id,
                    action_type=step.get("action", "step"),
                    action_payload=step.get("payload", ""),
                )
                
                try:
                    # Execute step via callback
                    result = await executor_fn(step) if asyncio.iscoroutinefunction(executor_fn) else executor_fn(step)
                    step_node.record_observation(str(result), success=True)
                    step_outputs.append(result)
                    executed_nodes.append(step_node)
                except Exception as ex:
                    logger.warning("Step failed on branch %s: %s", branch.branch_id, ex)
                    step_node.record_observation(str(ex), success=False)
                    tree.backtrack_to_parent(step_node.node_id)
                    tree.backtrack_to_parent(branch_node.node_id)
                    branch_success = False
                    last_error = str(ex)
                    break

            if branch_success:
                branch_node.record_observation("Branch completed successfully", success=True)
                return {
                    "success": True,
                    "branch_id": branch.branch_id,
                    "description": branch.description,
                    "steps_executed": len(step_outputs),
                    "outputs": step_outputs,
                    "state_tree_path": [n.action_payload for n in tree.get_execution_path()],
                }

        # If all candidate branches failed
        return {
            "success": False,
            "error": f"All candidate branches failed. Last error: {last_error}",
            "branches_evaluated": len(sorted_branches),
            "state_tree_nodes": len(tree.nodes),
        }
