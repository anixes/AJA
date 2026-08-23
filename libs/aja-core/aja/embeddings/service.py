"""
aja/embeddings/service.py
=============================
Phase 13 - Embedding Service.

Provides a unified `EmbeddingService` for converting text to vectors.
Includes an LRU cache (keyed by text hash) to guarantee fast (<10ms)
repeat retrievals.

Backend selection (resolution order — first match wins):
1. ``AJA_MOCK_EMBEDDINGS=1``            -> deterministic hashing mock.
2. ``AJA_EMBEDDING_BACKEND`` env var    -> "sentence_transformers" | "onnx" | "mock".
3. ``SwarmSettings.embedding_backend``  -> same values (default "auto").
4. "auto": ONNX via fastembed when available, else sentence-transformers.

ONNX backend note: we deliberately use fastembed with the model name
"sentence-transformers/all-MiniLM-L6-v2" (NOT "BAAI/bge-small-en-v1.5").
The bge model is also 384-dim but produces DIFFERENT vectors than MiniLM;
switching to it would require a full LanceDB reindex. Using MiniLM weights
through ONNX yields byte-compatible 384-dim vectors, so no reindex is needed
(the vector-dim check at aja/memory/vector.py:78 passes unchanged).

A raw-onnxruntime path (vendored .onnx weights + tokenizer) is intentionally
NOT implemented: doing it correctly requires tokenizer parity with the
sentence-transformers tokenizer; until those assets are vendored the explicit
"onnx" selection without fastembed raises an actionable RuntimeError instead
of silently returning incompatible vectors.
"""

from __future__ import annotations

import hashlib
import functools
import os
import random
from typing import List, Optional

# Model identifiers -----------------------------------------------------------
MINILM_MODEL_NAME = "all-MiniLM-L6-v2"
FASTEMBED_MINILM = "sentence-transformers/all-MiniLM-L6-v2"

VALID_BACKENDS = ("auto", "sentence_transformers", "onnx", "mock")


class OnnxBackendUnavailable(RuntimeError):
    """Raised when 'onnx' is explicitly selected but fastembed is not installed."""

# Singleton instances to avoid reloading models
_SENTENCE_MODEL = None
_ONNX_MODEL = None
_MODEL_LOADED = False

# Cached SwarmSettings.embedding_backend value (None = not yet read).
_CONFIG_BACKEND: Optional[str] = None


def _read_config_backend() -> str:
    """Reads embedding_backend from SwarmSettings, tolerating any load failure."""
    global _CONFIG_BACKEND
    if _CONFIG_BACKEND is not None:
        return _CONFIG_BACKEND
    try:
        from aja.config import load_and_validate_config

        cfg = load_and_validate_config()
        value = getattr(cfg.swarm_settings, "embedding_backend", "auto") or "auto"
    except Exception:  # best-effort: config may be absent/corrupt in test sandboxes
        value = "auto"
    _CONFIG_BACKEND = str(value).strip().lower()
    return _CONFIG_BACKEND


def _fastembed_available() -> bool:
    try:
        from fastembed import TextEmbedding  # noqa: F401

        return True
    except Exception:
        return False


def get_active_backend() -> str:
    """
    Resolves the active embedding backend.

    Returns one of "mock", "onnx", or "sentence_transformers".
    Resolution order: AJA_MOCK_EMBEDDINGS=1 > AJA_EMBEDDING_BACKEND env >
    SwarmSettings.embedding_backend field > auto detection.
    """
    if os.environ.get("AJA_MOCK_EMBEDDINGS") == "1":
        return "mock"

    raw = os.environ.get("AJA_EMBEDDING_BACKEND") or _read_config_backend()
    backend = (raw or "auto").strip().lower()
    if backend not in VALID_BACKENDS:
        print(
            f"[EmbeddingService] WARNING: unknown embedding backend '{raw}'. "
            f"Valid: {', '.join(VALID_BACKENDS)}. Falling back to 'auto'."
        )
        backend = "auto"

    if backend != "auto":
        return backend

    # auto: prefer the lightweight ONNX runtime when its package is present.
    return "onnx" if _fastembed_available() else "sentence_transformers"


def _reset_for_tests() -> None:
    """Drops cached singletons/config so env changes take effect (test helper)."""
    global _SENTENCE_MODEL, _ONNX_MODEL, _MODEL_LOADED, _CONFIG_BACKEND
    _SENTENCE_MODEL = None
    _ONNX_MODEL = None
    _MODEL_LOADED = False
    _CONFIG_BACKEND = None
    EmbeddingService.embed.cache_clear()


def _raise_onnx_unavailable() -> None:
    raise OnnxBackendUnavailable(
        "Embedding backend 'onnx' selected but the fastembed package is not "
        "installed. Setup step: pip install 'aja[vps]' (or 'pip install fastembed'). "
        "fastembed runs all-MiniLM-L6-v2 ONNX weights, producing identical "
        "384-dim vectors to sentence-transformers (no LanceDB reindex needed)."
    )


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

    def get_model_name(self) -> str:
        """Returns the identifier of the active embedding model."""
        backend = get_active_backend()
        if backend == "mock":
            return "mock-bag-of-words"
        self._load_model()
        if backend == "onnx" and _ONNX_MODEL is not None:
            return f"fastembed/{FASTEMBED_MINILM}"
        if _SENTENCE_MODEL is not None:
            return f"sentence-transformers/{MINILM_MODEL_NAME}"
        return "mock-bag-of-words"

    def embed_text(self, text: str) -> List[float]:
        """Alias for compatibility with planning modules."""
        return self.embed(text)

    def _load_model(self) -> None:
        global _SENTENCE_MODEL, _ONNX_MODEL, _MODEL_LOADED
        if _MODEL_LOADED:
            return

        try:
            if os.environ.get("AJA_MOCK_EMBEDDINGS") == "1":
                raise ImportError("Mock embeddings forced via environment variable.")

            backend = get_active_backend()

            if backend == "onnx":
                try:
                    # fastembed with MiniLM weights => vector-compatible with the
                    # sentence-transformers path (same model, same 384-dim output).
                    from fastembed import TextEmbedding
                except ImportError:
                    # Explicit selection must fail loudly rather than silently
                    # degrade vectors (which would corrupt LanceDB similarity).
                    if os.environ.get("AJA_EMBEDDING_BACKEND") == "onnx" or _read_config_backend() == "onnx":
                        _raise_onnx_unavailable()
                    raise
                print(f"[EmbeddingService] Loading ONNX embedding model ({FASTEMBED_MINILM}) via fastembed...")
                _ONNX_MODEL = TextEmbedding(model_name=FASTEMBED_MINILM)
            else:
                from sentence_transformers import SentenceTransformer

                # Small, fast model. 384 dimensions.
                print(f"[EmbeddingService] Loading sentence-transformers model ({MINILM_MODEL_NAME})...")
                _SENTENCE_MODEL = SentenceTransformer(MINILM_MODEL_NAME)
        except OnnxBackendUnavailable:
            # Explicit 'onnx' selection must fail loudly, never degrade vectors.
            _SENTENCE_MODEL = None
            _ONNX_MODEL = None
            raise
        except Exception as e:
            print(
                f"[EmbeddingService] WARNING: Could not load embedding backend "
                f"({type(e).__name__}: {e}). Falling back to deterministic mock embeddings."
            )
            _SENTENCE_MODEL = None
            _ONNX_MODEL = None
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

        global _SENTENCE_MODEL, _ONNX_MODEL
        if _ONNX_MODEL is not None:
            import numpy as np

            vec = next(iter(_ONNX_MODEL.embed([text])))
            if isinstance(vec, np.ndarray):
                return vec.tolist()
            return list(vec)
        elif _SENTENCE_MODEL is not None:
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

        words = re.findall(r"\b\w+\b", text.lower())
        for word in words:
            # Hash each word to a few indices
            seed = int(hashlib.sha256(word.encode("utf-8")).hexdigest()[:8], 16)
            rng = random.Random(seed)
            idx1 = rng.randint(0, self.dim - 1)
            idx2 = rng.randint(0, self.dim - 1)
            vec[idx1] += 1.0
            vec[idx2] += 1.0

        # Normalize to unit length (like cosine embeddings)
        import math

        mag = math.sqrt(sum(v * v for v in vec))
        if mag > 0:
            vec = [v / mag for v in vec]

        return vec
