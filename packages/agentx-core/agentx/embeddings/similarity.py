"""
agentx/embeddings/similarity.py
================================
Phase 13 - Vector Similarity Operations.
"""

from __future__ import annotations

import math
from typing import List


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """
    Compute cosine similarity between two vectors.
    
    Returns a float between -1.0 and 1.0 (or 0.0 if either vector has 0 magnitude).
    """
    if not vec_a or not vec_b:
        return 0.0
    
    if len(vec_a) != len(vec_b):
        raise ValueError("Vectors must have the same dimensionality.")

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot / (mag_a * mag_b)
