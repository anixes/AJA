"""
agent/embeddings/__init__.py
==============================
Phase 13 - Embeddings & Vector Search Package.

Exports the core embedding infrastructure:
  EmbeddingService  - singleton service for text embedding
  VectorIndex       - in-memory ANN index wrapper
  cosine_similarity - mathematical dot product / magnitudes
"""

from agent.embeddings.similarity import cosine_similarity
from agent.embeddings.service import EmbeddingService
from agent.embeddings.index import VectorIndex

__all__ = [
    "cosine_similarity",
    "EmbeddingService",
    "VectorIndex",
]
