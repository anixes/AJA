"""
agentx/planning/method_pruner.py
==================================
Phase 13 - Method Library Pruning (Embeddings).

Keeps the method store healthy by:
  1. Removing low-quality methods (score < threshold AND reuse_count > 2).
  2. Keeping only the top-K methods per task_type.
  3. Deduplicating semantically similar methods using cosine similarity on embeddings.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from agentx.planning.method_store import MethodStore
from agentx.embeddings.service import EmbeddingService
from agentx.embeddings.similarity import cosine_similarity

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MIN_SCORE: float = 0.20
DEFAULT_TOP_K: int = 20
DEFAULT_SIMILARITY_THRESHOLD: float = 0.85


# ---------------------------------------------------------------------------
# Pruner
# ---------------------------------------------------------------------------

def prune_methods(
    min_score: float = DEFAULT_MIN_SCORE,
    top_k_per_type: int = DEFAULT_TOP_K,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> int:
    """
    Prune the method library in three passes.

    Parameters
    ----------
    min_score : float
        Methods with score below this AND reuse_count > 2 are removed.
    top_k_per_type : int
        Maximum number of methods to keep per ``task_type``.
    similarity_threshold : float
        Embedding cosine similarity above which two methods are considered
        duplicates. The lower-scoring one is removed and its reuse_count
        is merged into the survivor.

    Returns
    -------
    int — total number of methods removed.
    """
    methods = MethodStore.load()
    before = len(methods)

    # Pass 1: Remove low-quality methods (but protect brand-new ones)
    methods = [
        m for m in methods
        if not (
            m.get("score", 0.5) < min_score
            and m.get("metrics", {}).get("reuse_count", 0) > 2
        )
    ]

    # Pass 2: Keep top-K per task_type
    by_type: Dict[str, List[Dict]] = {}
    for m in methods:
        t = m.get("task_type", "unknown")
        by_type.setdefault(t, []).append(m)

    methods = []
    for task_type, group in by_type.items():
        group.sort(key=lambda m: m.get("score", 0.0), reverse=True)
        methods.extend(group[:top_k_per_type])

    # Pass 3: Deduplicate by Embedding cosine similarity
    methods = _deduplicate(methods, similarity_threshold)

    removed = before - len(methods)
    MethodStore.save(methods)

    if removed > 0:
        print(f"[MethodPruner] Pruned {removed} method(s). Library size: {len(methods)}")

    return removed


def _deduplicate(
    methods: List[Dict],
    threshold: float,
) -> List[Dict]:
    """
    Merge methods whose embeddings are cosine-similar above threshold.

    Keeps the higher-scoring method; merges reuse_count into it.
    """
    if len(methods) < 2:
        return methods

    svc = EmbeddingService()

    # Track which indices to keep and which to merge
    merged_into: Dict[int, int] = {}  # idx → survivor_idx

    for i in range(len(methods)):
        if i in merged_into:
            continue
            
        vec_i = methods[i].get("embedding")
        if not vec_i:
            vec_i = svc.embed(methods[i].get("pattern", ""))
            
        for j in range(i + 1, len(methods)):
            if j in merged_into:
                continue
                
            vec_j = methods[j].get("embedding")
            if not vec_j:
                vec_j = svc.embed(methods[j].get("pattern", ""))
                
            sim = cosine_similarity(vec_i, vec_j)
            if sim >= threshold:
                # Decide winner by score
                score_i = methods[i].get("score", 0.0)
                score_j = methods[j].get("score", 0.0)
                survivor, duplicate = (i, j) if score_i >= score_j else (j, i)
                merged_into[duplicate] = survivor
                
                # Merge reuse_count into survivor
                dup_reuse = methods[duplicate].get("metrics", {}).get("reuse_count", 0)
                surv_metrics = methods[survivor].setdefault("metrics", {})
                surv_metrics["reuse_count"] = (
                    surv_metrics.get("reuse_count", 0) + dup_reuse
                )

    return [m for idx, m in enumerate(methods) if idx not in merged_into]

def dedup_single_method(method: Dict, library: List[Dict], threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> Tuple[bool, str]:
    """
    Checks if a single method is a duplicate of any existing method in the library.
    Returns (is_duplicate, duplicate_id).
    Used by MethodLearner before adding a new method.
    """
    svc = EmbeddingService()
    vec_i = method.get("embedding")
    if not vec_i:
        vec_i = svc.embed(method.get("pattern", ""))
        
    for k in library:
        vec_k = k.get("embedding")
        if not vec_k:
            vec_k = svc.embed(k.get("pattern", ""))
            
        sim = cosine_similarity(vec_i, vec_k)
        if sim >= threshold:
            return True, k.get("id", "unknown")
            
    return False, ""
