"""
agentx/embeddings/index.py
===========================
Phase 13 - In-Memory Vector Index.

A simple, fast in-memory store for vector searches. 
Uses exhaustive search (O(N)), which is extremely fast for <10,000 methods.
Can be seamlessly upgraded to FAISS or Annoy in the future without changing the API.
"""

from __future__ import annotations

from typing import List, Tuple
from agentx.embeddings.similarity import cosine_similarity


class VectorIndex:
    """
    Simple exact-match vector index for storing and retrieving methods.
    """

    def __init__(self):
        self.ids: List[str] = []
        self.vecs: List[List[float]] = []

    def clear(self) -> None:
        """Clear the index."""
        self.ids = []
        self.vecs = []

    def add(self, item_id: str, vec: List[float]) -> None:
        """
        Add or update a vector in the index.
        """
        if item_id in self.ids:
            # Update existing
            idx = self.ids.index(item_id)
            self.vecs[idx] = vec
        else:
            # Add new
            self.ids.append(item_id)
            self.vecs.append(vec)

    def remove(self, item_id: str) -> bool:
        """Remove an item by ID. Returns True if removed."""
        try:
            idx = self.ids.index(item_id)
            self.ids.pop(idx)
            self.vecs.pop(idx)
            return True
        except ValueError:
            return False

    def search(self, query_vec: List[float], k: int = 5) -> List[Tuple[str, float]]:
        """
        Search for the top-K most similar vectors in the index using cosine similarity.
        
        Returns a list of (id, similarity) tuples, sorted highest to lowest.
        """
        if not self.ids or not query_vec:
            return []

        results = []
        for i, vec in enumerate(self.vecs):
            sim = cosine_similarity(query_vec, vec)
            results.append((self.ids[i], sim))

        # Sort descending by similarity
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:k]
