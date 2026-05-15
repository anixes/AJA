"""
agentx/decision/disagreement.py
===============================
Phase 21: Disagreement-Aware Consensus + Minority Veto Layer.
"""

from typing import List
from itertools import combinations
from agentx.planning.models import PlanGraph

def plan_similarity(p1: PlanGraph, p2: PlanGraph) -> float:
    """Jaccard similarity based on primitive node IDs."""
    nodes1 = {n.id for n in p1.primitive_nodes()}
    nodes2 = {n.id for n in p2.primitive_nodes()}
    inter = len(nodes1.intersection(nodes2))
    union = len(nodes1.union(nodes2))
    return inter / union if union > 0 else 0.0

def disagreement_score(plans: List[PlanGraph]) -> float:
    """
    0 → identical
    1 → completely different
    """
    if len(plans) < 2:
        return 0.0
    similarities = []
    for i in range(len(plans)):
        for j in range(i+1, len(plans)):
            similarities.append(plan_similarity(plans[i], plans[j]))
    return 1.0 - (sum(similarities) / len(similarities))

def dependency_mismatch(p1: PlanGraph, p2: PlanGraph) -> bool:
    """Check if plans have fundamentally different dependencies."""
    deps1 = {tuple(sorted(n.dependencies)) for n in p1.primitive_nodes()}
    deps2 = {tuple(sorted(n.dependencies)) for n in p2.primitive_nodes()}
    # Return true if disjoint or very different (simple check: any difference)
    return deps1 != deps2

def state_mismatch(p1: PlanGraph, p2: PlanGraph) -> bool:
    """Check if plans have different preconditions/effects."""
    def extract_state_keys(p: PlanGraph):
        preconds = set()
        effects = set()
        for n in p.primitive_nodes():
            if n.preconditions:
                preconds.update(n.preconditions.keys())
            if n.effects:
                effects.update(n.effects.keys())
        return preconds, effects

    p1_pre, p1_eff = extract_state_keys(p1)
    p2_pre, p2_eff = extract_state_keys(p2)
    return (p1_pre != p2_pre) or (p1_eff != p2_eff)

def detect_conflicts(plans: List[PlanGraph]) -> List[str]:
    conflicts = []
    for p1, p2 in combinations(plans, 2):
        if dependency_mismatch(p1, p2):
            conflicts.append("dependency_conflict")
        if state_mismatch(p1, p2):
            conflicts.append("state_conflict")
    return list(set(conflicts))

def classify_disagreement(score: float, conflicts: List[str]) -> str:
    if score < 0.2 and not conflicts:
        return "LOW"
    elif score < 0.5:
        return "MEDIUM"
    else:
        return "HIGH"

def is_outlier(p: PlanGraph, plans: List[PlanGraph]) -> bool:
    """Check if a plan strongly contradicts the others."""
    if len(plans) < 3:
        return False
    # Calculate average similarity to other plans
    similarities = [plan_similarity(p, other) for other in plans if other is not p]
    avg_sim = sum(similarities) / len(similarities)
    
    # If this plan is very dissimilar to the others (< 0.2 similarity) but others agree more
    # Or just if avg_sim is very low compared to the group's avg
    return avg_sim < 0.3

def minority_veto(plans: List[PlanGraph]) -> bool:
    """If ANY plan strongly contradicts others → trigger veto."""
    for p in plans:
        if is_outlier(p, plans):
            return True
    return False

# Global metrics tracker for Phase 21
metrics = {
    "disagreement_rate": 0.0,
    "veto_trigger_rate": 0.0,
    "false_consensus_rate": 0.0,
    "total_consensus_runs": 0,
    "total_vetoes": 0,
    "total_disagreements": 0
}

def update_disagreement_metrics(score: float, veto: bool, false_consensus: bool):
    global metrics
    metrics["total_consensus_runs"] += 1
    if score > 0.3:
        metrics["total_disagreements"] += 1
    if veto:
        metrics["total_vetoes"] += 1
        
    total = metrics["total_consensus_runs"]
    metrics["disagreement_rate"] = metrics["total_disagreements"] / total
    metrics["veto_trigger_rate"] = metrics["total_vetoes"] / total
    
    if false_consensus:
        # Simplistic tracking for false consensus
        current_fc = metrics["false_consensus_rate"] * (total - 1)
        metrics["false_consensus_rate"] = (current_fc + 1) / total
    else:
        metrics["false_consensus_rate"] = (metrics["false_consensus_rate"] * (total - 1)) / total
