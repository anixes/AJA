"""
agentx/planning/method_learner.py
====================================
Phase 12 - Controlled Method Learning.

Extracts reusable method templates from successful plan executions and
stores them in the MethodStore.

Design principles
-----------------
* Eligibility gates prevent low-quality plans from polluting the library.
* Normalization replaces concrete values (paths, IDs, URLs) with
  template variables so the stored method generalizes across goals.
* Deduplication merges semantically similar patterns rather than creating
  redundant entries.
* Pruning is triggered automatically when the library grows too large.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Thresholds (tunable)
# ---------------------------------------------------------------------------

LEARNING_THRESHOLD: float = 0.60    # Minimum plan score to extract a method
MIN_STABILITY: float = 0.50         # Not used at first-use but checked on library entry
MAX_LIBRARY_SIZE: int = 50          # Trigger prune if exceeded
DEDUP_SIMILARITY_THRESHOLD: float = 0.85  # TF-IDF cosine above which patterns are merged


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def is_eligible(plan, success: bool, score: float) -> bool:
    """
    Return True if a plan is suitable for method extraction.

    Parameters
    ----------
    plan : PlanGraph
        The executed plan.
    success : bool
        Whether all nodes completed successfully.
    score : float
        Composite plan quality score (e.g., 1 - avg_uncertainty).
    """
    if not success:
        return False
    if score < LEARNING_THRESHOLD:
        return False
    primitives = plan.primitive_nodes()
    # Must have between 2 and 10 primitive steps (too simple or too complex → skip)
    if len(primitives) < 2 or len(primitives) > 10:
        return False
    return True


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------

# Order matters — paths before IDs to avoid overlap
_NORMALIZERS = [
    # Absolute Windows paths  (C:\..., D:\...)
    (re.compile(r"[A-Za-z]:\\[^\s,\"']+"), "{{path}}"),
    # Absolute POSIX paths
    (re.compile(r"/(?:[a-zA-Z0-9._-]+/)+[a-zA-Z0-9._-]*"), "{{path}}"),
    # URLs (http/https)
    (re.compile(r"https?://[^\s,\"']+"), "{{url}}"),
    # UUIDs
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "{{id}}"),
    # Long numeric IDs (8+ digits)
    (re.compile(r"\b\d{8,}\b"), "{{id}}"),
]


def _normalize_value(value: str) -> str:
    """Replace concrete values in a string with template variables."""
    for pattern, replacement in _NORMALIZERS:
        value = pattern.sub(replacement, value)
    return value


def _normalize_node(node_dict: Dict) -> Dict:
    """Recursively normalize string values in a node dict."""
    result = {}
    for k, v in node_dict.items():
        if isinstance(v, str):
            result[k] = _normalize_value(v)
        elif isinstance(v, dict):
            result[k] = {dk: (_normalize_value(dv) if isinstance(dv, str) else dv)
                         for dk, dv in v.items()}
        elif isinstance(v, list):
            result[k] = [
                (_normalize_value(item) if isinstance(item, str) else item)
                for item in v
            ]
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Task type inference
# ---------------------------------------------------------------------------

_TASK_VERBS = [
    "deploy", "build", "test", "run", "install", "configure", "create",
    "update", "delete", "fetch", "analyse", "analyze", "verify", "migrate",
    "monitor", "scale", "restart", "backup", "restore", "debug",
]


def _infer_task_type(goal: str) -> str:
    """Return the first recognized action verb from the goal, else 'general'."""
    lower = goal.lower()
    for verb in _TASK_VERBS:
        if re.search(r"\b" + verb + r"\b", lower):
            return verb
    return "general"


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _goal_to_id(goal: str) -> str:
    """
    Create a snake_case method ID from the first 8 words of the goal.
    Truncated to 64 characters; appends a short UUID fragment for uniqueness.
    """
    words = re.sub(r"[^a-z0-9\s]", "", goal.lower()).split()[:8]
    slug = "_".join(words)[:56]  # leave room for suffix
    suffix = uuid.uuid4().hex[:6]
    return f"{slug}_{suffix}"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_method(plan, goal: str) -> Dict:
    """
    Convert a successful PlanGraph into a storable method template.

    Parameters
    ----------
    plan : PlanGraph
        The executed plan to extract from.
    goal : str
        The original goal string (used for pattern + task_type).

    Returns
    -------
    dict — a complete Phase 12 method entry with initialized metrics.
    """
    from agentx.planning.method_scorer import score_method

    # Build normalized plan_template
    raw_template = plan.to_dict()
    normalized_nodes = [_normalize_node(n) for n in raw_template.get("nodes", [])]
    plan_template = {
        "goal": _normalize_value(raw_template.get("goal", goal)),
        "nodes": normalized_nodes,
    }

    method: Dict = {
        "id": _goal_to_id(goal),
        "task_type": _infer_task_type(goal),
        "pattern": goal,
        "plan_template": plan_template,
        "metrics": {
            "success_rate": 1.0,
            "avg_uncertainty": _avg_uncertainty(plan),
            "avg_latency": 0.0,
            "reuse_count": 1,
            "stability": 1.0,
        },
        "score": 0.0,  # computed below
        "last_used": datetime.now(timezone.utc).isoformat(),
    }
    method["score"] = score_method(method)
    return method


def _avg_uncertainty(plan) -> float:
    primitives = plan.primitive_nodes()
    if not primitives:
        return 0.5
    return sum(n.uncertainty for n in primitives) / len(primitives)


# ---------------------------------------------------------------------------
# Learning entry point
# ---------------------------------------------------------------------------

def learn_method(plan, goal: str, success: bool, score: float) -> bool:
    """
    Evaluate a plan for learning eligibility and store a method if suitable.

    Parameters
    ----------
    plan : PlanGraph
        The completed plan.
    goal : str
        The original goal string.
    success : bool
        Whether execution succeeded.
    score : float
        Composite quality score for the plan.

    Returns
    -------
    bool — True if a method was stored/merged, False if skipped.
    """
    if not is_eligible(plan, success, score):
        return False

    from agentx.planning.method_store import MethodStore
    from agentx.planning.method_scorer import update_metrics
    from agentx.planning.method_pruner import prune_methods
    from agentx.embeddings.service import EmbeddingService

    # 1. Embed the goal for the new method
    svc = EmbeddingService()
    emb = svc.embed(goal)

    new_method = extract_method(plan, goal)
    new_method["embedding"] = emb

    # Deduplication: check against existing index
    index = MethodStore.get_index()
    candidates = index.search(emb, k=1)
    
    best_sim = 0.0
    best_match_id = None
    
    if candidates:
        best_match_id, best_sim = candidates[0]

    if best_sim >= DEDUP_SIMILARITY_THRESHOLD and best_match_id is not None:
        best_match = MethodStore.get_by_id(best_match_id)
        if best_match:
            # Merge into existing method: update metrics with one success observation
            merged = update_metrics(
                best_match,
                success=True,
                latency=0.0,
                uncertainty=new_method["metrics"]["avg_uncertainty"],
            )
            MethodStore.upsert(merged)
            print(
                f"[MethodLearner] Merged with existing method '{best_match.get('id')}' "
                f"(similarity={best_sim:.3f})"
            )
        else:
            MethodStore.upsert(new_method)
            print(f"[MethodLearner] Stored new method '{new_method['id']}' (score={new_method['score']:.3f})")
    else:
        MethodStore.upsert(new_method)
        print(f"[MethodLearner] Stored new method '{new_method['id']}' (score={new_method['score']:.3f})")

    # Prune if library is oversized
    if MethodStore.count() > MAX_LIBRARY_SIZE:
        prune_methods()

    return True
