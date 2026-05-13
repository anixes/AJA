"""
agentx/planning/method_retriever.py
=====================================
Phase 13 - Method Retrieval & Fit Scoring (Embeddings).

Implements two public functions:

  retrieve_methods(goal, top_n)
    Returns the top-N most relevant methods from the library for a given
    goal string, using the VectorIndex (cosine similarity on semantic embeddings).

  method_fit(method, goal_sim, current_state)
    Computes a single fit score [0, 1] combining semantic similarity,
    historical success rate, and precondition compatibility with the
    current execution state.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from agentx.planning.method_store import MethodStore
from agentx.embeddings.service import EmbeddingService

# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def retrieve_methods(goal: str, top_n: int = 5) -> List[Tuple[Dict, float]]:
    """
    Return the top-N methods most relevant to ``goal``.

    Parameters
    ----------
    goal : str
        The planning goal text to match against method embeddings.
    top_n : int
        Maximum number of candidates to return.

    Returns
    -------
    List of (method_dict, similarity_score) sorted by semantic similarity.
    """
    # Trigger load (and index build) if necessary
    methods = MethodStore.load()
    if not methods:
        return []

    index = MethodStore.get_index()
    svc = EmbeddingService()
    
    query_vec = svc.embed(goal)
    candidates = index.search(query_vec, k=top_n * 2) # Get some extra to filter

    results = []
    # Map index results back to full method objects
    for mid, sim in candidates:
        if sim < 0.15:
            continue
        m = MethodStore.get_by_id(mid)
        if m:
            results.append((m, sim))
            if len(results) == top_n:
                break
            
    return results


# ---------------------------------------------------------------------------
# Fit scoring
# ---------------------------------------------------------------------------

def method_fit(method: Dict, goal_sim: float, current_state: Dict) -> float:
    """
    Score how well a method fits a specific goal and current system state.

    Components
    ----------
    * 0.40 — goal_sim (Cosine similarity between method embedding and goal)
    * 0.25 — Method overall score (from metrics)
    * 0.20 — Precondition compatibility with current_state
    * 0.15 — Historical success_rate

    Parameters
    ----------
    method : dict
        A Phase 12/13 method entry.
    goal_sim : float
        The semantic similarity between the method and the goal.
    current_state : dict
        The current system_state dict at the time of planning.

    Returns
    -------
    float in [0, 1].
    """
    metrics = method.get("metrics", {})
    success_rate = float(metrics.get("success_rate", 0.5))
    method_score = float(method.get("score", 0.4))

    compat = _precondition_compat(method, current_state)

    return (
        0.40 * goal_sim
        + 0.25 * method_score
        + 0.20 * compat
        + 0.15 * success_rate
    )


def _precondition_compat(method: Dict, current_state: Dict) -> float:
    """
    Fraction of root-node preconditions in the plan_template satisfied by
    the current_state.

    If the template has no preconditions, returns 1.0 (fully compatible).
    """
    template = method.get("plan_template", {})
    nodes = template.get("nodes", [])
    if not nodes:
        return 1.0

    # Find root nodes (those with no dependencies)
    dep_targets = {dep for n in nodes for dep in n.get("dependencies", [])}
    roots = [n for n in nodes if n.get("id") not in dep_targets and not n.get("dependencies")]

    all_preconditions: Dict = {}
    for root in roots:
        all_preconditions.update(root.get("preconditions", {}))

    if not all_preconditions:
        return 1.0

    satisfied = sum(
        1 for k, v in all_preconditions.items()
        if current_state.get(k) == v
    )
    return satisfied / len(all_preconditions)


def _precondition_compat(method: Dict, current_state: Dict) -> float:
    """
    Fraction of root-node preconditions in the plan_template satisfied by
    the current_state.

    If the template has no preconditions, returns 1.0 (fully compatible).
    """
    template = method.get("plan_template", {})
    nodes = template.get("nodes", [])
    if not nodes:
        return 1.0

    # Find root nodes (those with no dependencies)
    dep_targets = {dep for n in nodes for dep in n.get("dependencies", [])}
    roots = [n for n in nodes if n.get("id") not in dep_targets and not n.get("dependencies")]

    all_preconditions: Dict = {}
    for root in roots:
        all_preconditions.update(root.get("preconditions", {}))

    if not all_preconditions:
        return 1.0

    satisfied = sum(
        1 for k, v in all_preconditions.items()
        if current_state.get(k) == v
    )
    return satisfied / len(all_preconditions)
