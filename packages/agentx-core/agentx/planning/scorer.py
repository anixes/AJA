"""
agentx/planning/scorer.py
==========================
Phase 14 - Cost-Aware Scoring & Complexity Estimation.

Provides heuristics to estimate goal complexity before generation,
and scores generated plans based on predicted cost, risk, and success.
"""

from __future__ import annotations

import math
from typing import Dict, Any

from agentx.planning.models import PlanGraph

# Complexity levels
COMPLEXITY_LOW = "LOW"
COMPLEXITY_MEDIUM = "MEDIUM"
COMPLEXITY_HIGH = "HIGH"

def estimate_complexity(goal: str) -> str:
    """
    Estimates the complexity of a goal.
    
    Uses:
    - Length of goal (word count)
    - Retrieval difficulty (based on semantic spread, if possible)
    """
    words = len(goal.split())
    
    # 1. Simple heuristic: word count
    if words < 5:
        score = 1
    elif words < 12:
        score = 2
    else:
        score = 3
        
    # 2. Heuristic: specific task verbs usually indicate specific complexity
    high_complexity_verbs = {"migrate", "deploy", "architect", "refactor"}
    low_complexity_verbs = {"read", "fetch", "get", "list", "print"}
    
    lower_goal = goal.lower()
    for verb in high_complexity_verbs:
        if verb in lower_goal:
            score += 1
            break
            
    for verb in low_complexity_verbs:
        if verb in lower_goal:
            score -= 1
            break
            
    if score <= 1:
        return COMPLEXITY_LOW
    elif score == 2:
        return COMPLEXITY_MEDIUM
    else:
        return COMPLEXITY_HIGH


def score_plan(plan: PlanGraph, verifier_score: float, is_from_method: bool = False, method_success_rate: float = 0.5, disagreement_penalty: float = 0.0) -> float:
    """
    Cost-aware scoring function for a PlanGraph.
    
    Weights:
    - 0.25 * (1 - uncertainty)
    - 0.20 * success_probability (from method history, or default)
    - 0.15 * parallelism_score
    - 0.15 * method_reuse (is_from_method)
    - 0.15 * (1 - estimated_cost)
    - 0.10 * verifier_score (state consistency / correctness)
    """
    # 1. Uncertainty
    primitives = plan.primitive_nodes()
    if primitives:
        avg_uncertainty = sum(n.uncertainty for n in primitives) / len(primitives)
    else:
        avg_uncertainty = 1.0
        
    # 2. Parallelism Score
    # Ratio of max depth to total nodes (lower depth = higher parallelism)
    total_nodes = len(primitives)
    if total_nodes <= 1:
        parallelism = 0.0
    else:
        # A rough heuristic for depth
        edges = len(plan.edges)
        # Highly sequential: edges ~ total_nodes - 1
        # Highly parallel: edges ~ 0
        seq_ratio = min(edges / max(total_nodes - 1, 1), 1.0)
        parallelism = 1.0 - seq_ratio
        
    # 3. Method reuse
    reuse_bonus = 1.0 if is_from_method else 0.0
    
    # 4. Estimated Cost
    # Simple heuristic: more nodes = higher cost. Max cap at 10 nodes.
    cost = min(total_nodes / 10.0, 1.0)
    
    score = (
        0.25 * (1.0 - avg_uncertainty) +
        0.20 * method_success_rate +
        0.15 * parallelism +
        0.15 * reuse_bonus +
        0.15 * (1.0 - cost) +
        0.10 * verifier_score
    )
    
    # Phase 14 Wave 4 / Phase 23 / Phase 26: Apply Failure Memory Penalty
    from agentx.memory.failure_memory import failure_memory
    penalty = failure_memory.similarity_to_failed_plans(plan)
    if penalty > 0.8: # high similarity to failed plan
        print(f"[Scorer] Similar failed plan detected. Applying penalty of 1.0.")
        score -= 1.0
    elif penalty > 0:
        score -= penalty
        
    # Phase 26: RL-lite Policy Bonus
    try:
        from agentx.rl.policy_store import policy_store
        policy_bonus = policy_store.get_plan_bonus(plan)
        score += policy_bonus
    except Exception:
        pass
        
    # Phase 21: Apply Disagreement Penalty
    if disagreement_penalty > 0:
        print(f"[Scorer] Applying disagreement penalty of {disagreement_penalty:.2f} to candidate.")
        score -= disagreement_penalty
    
    return max(0.0, min(1.0, score))
