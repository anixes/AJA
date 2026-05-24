"""
aja/embeddings/service.py
=============================
Phase 13 - Embedding Service.

Provides a unified `EmbeddingService` for converting text to vectors.
Includes an LRU cache (keyed by text hash) to guarantee fast (<10ms)
repeat retrievals.

Uses `sentence-transformers` if available, falling back to a deterministic
hashing mock to ensure environments without ML dependencies (or tests) still function.
"""

from __future__ import annotations

import hashlib
import functools
import random
from typing import List

# Singleton instance of the model to avoid reloading
_SENTENCE_MODEL = None
_MODEL_LOADED = False


class EmbeddingService:
    """
    Singleton service for text embedding.
    Caches responses to ensure extremely fast lookups for known strings.
    """
    
    def __init__(self, dim: int = 384):
        """
        Initializes the service. 
        `dim` is used primarily for the mock fallback if the model is missing.
        """
        self.dim = dim

    def embed_text(self, text: str) -> List[float]:
        """Alias for compatibility with planning modules."""
        return self.embed(text)

    def _load_model(self) -> None:
        global _SENTENCE_MODEL, _MODEL_LOADED
        if _MODEL_LOADED:
            return

        try:
            # Check for memory pressure before even trying
            import os
            if os.environ.get("AJA_MOCK_EMBEDDINGS") == "1":
                raise ImportError("Mock embeddings forced via environment variable.")

            from sentence_transformers import SentenceTransformer
            # Small, fast model. 384 dimensions.
            print("[EmbeddingService] Loading sentence-transformers model (all-MiniLM-L6-v2)...")
            _SENTENCE_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as e:
            print(f"[EmbeddingService] WARNING: Could not load sentence-transformers ({type(e).__name__}: {e}). Falling back to deterministic mock embeddings.")
            _SENTENCE_MODEL = None
        finally:
            _MODEL_LOADED = True

    @functools.lru_cache(maxsize=1024)
    def embed(self, text: str) -> List[float]:
        """
        Convert text into a dense vector representation.
        Results are LRU-cached for maximum speed.
        """
        if not text or not text.strip():
            return [0.0] * self.dim

        self._load_model()

        global _SENTENCE_MODEL
        if _SENTENCE_MODEL is not None:
            # sentence-transformers returns a numpy array, convert to standard python float list
            import numpy as np
            vec = _SENTENCE_MODEL.encode(text)
            if isinstance(vec, np.ndarray):
                return vec.tolist()
            return list(vec)
        else:
            return self._mock_embed(text)

    def _mock_embed(self, text: str) -> List[float]:
        """
        Deterministic mock embedding using word hashing.
        This provides a simple bag-of-words-like property so tests testing 
        semantic overlap will see non-zero similarities.
        """
        vec = [0.0] * self.dim
        if not text:
            return vec
            
        import re
        words = re.findall(r'\b\w+\b', text.lower())
        for word in words:
            # Hash each word to a few indices
            import hashlib
            seed = int(hashlib.sha256(word.encode("utf-8")).hexdigest()[:8], 16)
            import random
            rng = random.Random(seed)
            idx1 = rng.randint(0, self.dim - 1)
            idx2 = rng.randint(0, self.dim - 1)
            vec[idx1] += 1.0
            vec[idx2] += 1.0
            
        # Normalize to unit length (like cosine embeddings)
        import math
        mag = math.sqrt(sum(v*v for v in vec))
        if mag > 0:
            vec = [v / mag for v in vec]
            
        return vec
