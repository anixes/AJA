"""
aja/embeddings/service.py
============================
Phase 13 - Embedding Service (hardened).

Provides a unified `EmbeddingService` for converting text to vectors.

Backend selection (resolution order — first match wins):
1. ``AJA_MOCK_EMBEDDINGS=1``            -> deterministic hashing mock.
2. ``AJA_EMBEDDING_BACKEND`` env var    -> "sentence_transformers" | "onnx" | "mock".
3. ``SwarmSettings.embedding_backend``  -> same values (default "auto").
4. "auto": ONNX via fastembed when available, else sentence-transformers.

Failure semantics: when a REAL backend is selected and fails to load, this
service raises :class:`OnnxBackendUnavailable` instead of silently degrading
to mock embeddings — silent degradation would poison persistent LanceDB
vector spaces with hash-mock vectors. The mock backend is available ONLY via
explicit selection (``AJA_MOCK_EMBEDDINGS=1`` or ``AJA_EMBEDDING_BACKEND=mock``),
which is how the test suite consumes it.

ONNX backend note: we deliberately use fastembed with the model name
"sentence-transformers/all-MiniLM-L6-v2" (NOT "BAAI/bge-small-en-v1.5").
The bge model is also 384-dim but produces DIFFERENT vectors than MiniLM;
switching to it would require a full LanceDB reindex. Using MiniLM weights
through ONNX yields byte-compatible 384-dim vectors, so no reindex is needed
(the vector-dim check at aja/memory/vector.py:78 passes unchanged).
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import random
import re
import threading
import warnings
from collections import OrderedDict
from typing import List, Optional

logger = logging.getLogger(__name__)

# Model identifiers -----------------------------------------------------------
# Registry of supported embedding models. All entries MUST be 384-dim so the
# fixed-size LanceDB Arrow columns (memory/vector.py) stay valid. Switching
# models changes the vector space: run 'aja reindex-embeddings' afterwards.
EMBEDDING_MODELS: dict = {
    "all-MiniLM-L6-v2": {
        "st": "all-MiniLM-L6-v2",
        "fastembed": "sentence-transformers/all-MiniLM-L6-v2",
    },
    "bge-small-en-v1.5": {
        "st": "BAAI/bge-small-en-v1.5",
        "fastembed": "BAAI/bge-small-en-v1.5",
    },
}
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Back-compat constants (default model identifiers).
MINILM_MODEL_NAME = DEFAULT_EMBEDDING_MODEL
FASTEMBED_MINILM = EMBEDDING_MODELS[DEFAULT_EMBEDDING_MODEL]["fastembed"]

VALID_BACKENDS = ("auto", "sentence_transformers", "onnx", "mock")

_CACHE_CAPACITY = 4096


class OnnxBackendUnavailable(RuntimeError):
    """Raised when the selected embedding backend cannot be loaded."""


# Singleton instances to avoid reloading models
_SENTENCE_MODEL = None
_ONNX_MODEL = None
_MODEL_LOADED = False

# Cached SwarmSettings.embedding_backend value (None = not yet read).
_CONFIG_BACKEND: Optional[str] = None
# Cached SwarmSettings.embedding_model value (None = not yet read).
_CONFIG_MODEL: Optional[str] = None

# Process-wide service singleton + text-keyed vector cache.
_SERVICE: Optional["EmbeddingService"] = None
_CACHE_LOCK = threading.Lock()
_VECTOR_CACHE: "OrderedDict[str, List[float]]" = OrderedDict()


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


def _read_config_model() -> str:
    """Reads embedding_model from SwarmSettings, tolerating any load failure."""
    global _CONFIG_MODEL
    if _CONFIG_MODEL is not None:
        return _CONFIG_MODEL
    try:
        from aja.config import load_and_validate_config

        cfg = load_and_validate_config()
        value = (
            getattr(cfg.swarm_settings, "embedding_model", DEFAULT_EMBEDDING_MODEL)
            or DEFAULT_EMBEDDING_MODEL
        )
    except Exception:  # best-effort: config may be absent/corrupt in test sandboxes
        value = DEFAULT_EMBEDDING_MODEL
    model = str(value).strip()
    if model not in EMBEDDING_MODELS:
        logger.warning(
            "Unknown embedding model '%s'. Valid: %s. Falling back to '%s'.",
            model,
            ", ".join(EMBEDDING_MODELS),
            DEFAULT_EMBEDDING_MODEL,
        )
        model = DEFAULT_EMBEDDING_MODEL
    _CONFIG_MODEL = model
    return _CONFIG_MODEL


def get_active_model() -> str:
    """Returns the canonical model key (e.g. 'all-MiniLM-L6-v2')."""
    if os.environ.get("AJA_MOCK_EMBEDDINGS") == "1":
        return DEFAULT_EMBEDDING_MODEL
    override = os.environ.get("AJA_EMBEDDING_MODEL")
    if override:
        model = override.strip()
        if model in EMBEDDING_MODELS:
            return model
        logger.warning(
            "Unknown AJA_EMBEDDING_MODEL '%s'. Valid: %s. Using config/default.",
            override,
            ", ".join(EMBEDDING_MODELS),
        )
    return _read_config_model()


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
        logger.warning(
            "Unknown embedding backend '%s'. Valid: %s. Falling back to 'auto'.",
            raw,
            ", ".join(VALID_BACKENDS),
        )
        backend = "auto"

    if backend != "auto":
        return backend

    # auto: prefer the lightweight ONNX runtime when its package is present.
    return "onnx" if _fastembed_available() else "sentence_transformers"


def get_embedding_service() -> "EmbeddingService":
    """Returns the process-wide EmbeddingService singleton.

    A shared singleton matters because the vector cache is keyed on text
    alone; per-call instances would fragment it.
    """
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = EmbeddingService()
    return _SERVICE


def _cache_get(text: str) -> Optional[List[float]]:
    with _CACHE_LOCK:
        vec = _VECTOR_CACHE.get(text)
        if vec is not None:
            _VECTOR_CACHE.move_to_end(text)
        return vec


def _cache_put(text: str, vec: List[float]) -> None:
    with _CACHE_LOCK:
        _VECTOR_CACHE[text] = vec
        _VECTOR_CACHE.move_to_end(text)
        while len(_VECTOR_CACHE) > _CACHE_CAPACITY:
            _VECTOR_CACHE.popitem(last=False)


def _reset_for_tests() -> None:
    """Drops cached singletons/config so env changes take effect (test helper)."""
    global _SENTENCE_MODEL, _ONNX_MODEL, _MODEL_LOADED, _CONFIG_BACKEND, _CONFIG_MODEL, _SERVICE
    _SENTENCE_MODEL = None
    _ONNX_MODEL = None
    _MODEL_LOADED = False
    _CONFIG_BACKEND = None
    _CONFIG_MODEL = None
    _SERVICE = None
    with _CACHE_LOCK:
        _VECTOR_CACHE.clear()


def _raise_onnx_unavailable() -> None:
    raise OnnxBackendUnavailable(
        "Embedding backend 'onnx' selected but the fastembed package is not "
        "installed. Setup step: pip install 'aja[vps]' (or 'pip install fastembed'). "
        "fastembed runs all-MiniLM-L6-v2 ONNX weights, producing identical "
        "384-dim vectors to sentence-transformers (no LanceDB reindex needed)."
    )


class EmbeddingService:
    """Unified text-embedding gateway with a shared process-wide vector cache.

    Prefer :func:`get_embedding_service()` over constructing instances
    directly — the cache is global, but a shared service keeps intent clear.
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
        model_key = get_active_model()
        spec = EMBEDDING_MODELS[model_key]
        if backend == "onnx" and _ONNX_MODEL is not None:
            return f"fastembed/{spec['fastembed']}"
        if _SENTENCE_MODEL is not None:
            return f"sentence-transformers/{spec['st']}"
        return "mock-bag-of-words"

    def embed_text(self, text: str) -> List[float]:
        """Alias for compatibility with planning modules."""
        return self.embed(text)

    def embed(self, text: str) -> List[float]:
        """Convert text into a dense vector (cached, thread-safe)."""
        if not text or not text.strip():
            return [0.0] * self.dim
        cached = _cache_get(text)
        if cached is not None:
            return cached
        vec = self._compute_embed(text)
        _cache_put(text, vec)
        return vec

    def embed_many(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts with one backend call for cache misses.

        Empty/whitespace entries map to zero vectors without touching the
        backend. Results are stored in the shared cache.
        """
        results: List[Optional[List[float]]] = [None] * len(texts)
        missing: List[int] = []
        for i, text in enumerate(texts):
            if not text or not text.strip():
                results[i] = [0.0] * self.dim
                continue
            cached = _cache_get(text)
            if cached is not None:
                results[i] = cached
            else:
                missing.append(i)

        if missing:
            self._load_model()
            batch_texts = [texts[i] for i in missing]
            if _ONNX_MODEL is not None:
                import numpy as np

                vectors = list(_ONNX_MODEL.embed(batch_texts))
                computed = [
                    v.tolist() if isinstance(v, np.ndarray) else list(v)
                    for v in vectors
                ]
            elif _SENTENCE_MODEL is not None:
                import numpy as np

                vectors = _SENTENCE_MODEL.encode(batch_texts)
                computed = [
                    v.tolist() if isinstance(v, np.ndarray) else list(v)
                    for v in vectors
                ]
            else:
                computed = [self._mock_embed(t) for t in batch_texts]
            for i, vec in zip(missing, computed):
                _cache_put(texts[i], vec)
                results[i] = vec

        return [vec for vec in results if vec is not None]

    def _load_model(self) -> None:
        global _SENTENCE_MODEL, _ONNX_MODEL, _MODEL_LOADED
        if _MODEL_LOADED:
            return

        if os.environ.get("AJA_MOCK_EMBEDDINGS") == "1":
            _MODEL_LOADED = True
            return

        backend = get_active_backend()
        model_key = get_active_model()
        spec = EMBEDDING_MODELS[model_key]
        try:
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
                logger.info("Loading ONNX embedding model (%s) via fastembed...", spec["fastembed"])
                _ONNX_MODEL = TextEmbedding(model_name=spec["fastembed"])
            elif backend == "sentence_transformers":
                os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
                os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    try:
                        import huggingface_hub.utils

                        huggingface_hub.utils.disable_progress_bars()
                    except Exception:
                        pass
                    from sentence_transformers import SentenceTransformer

                    # Small, fast model. 384 dimensions.
                    logger.info("Loading sentence-transformers model (%s)...", spec["st"])
                    _SENTENCE_MODEL = SentenceTransformer(spec["st"])
            else:  # explicit "mock" selection without the env flag
                _MODEL_LOADED = True
                return
        except OnnxBackendUnavailable:
            raise
        except Exception as e:
            # Fail loudly: silent mock fallback would write garbage vectors
            # into persistent LanceDB tables and corrupt similarity search.
            raise OnnxBackendUnavailable(
                f"Embedding backend '{backend}' failed to load "
                f"({type(e).__name__}: {e}). Install a real backend with "
                f"'pip install fastembed' (lightweight ONNX) or "
                f"'pip install sentence-transformers' (torch), or force the "
                f"test mock with AJA_MOCK_EMBEDDINGS=1."
            ) from e
        _MODEL_LOADED = True

    def _compute_embed(self, text: str) -> List[float]:
        self._load_model()
        if _ONNX_MODEL is not None:
            import numpy as np

            vec = next(iter(_ONNX_MODEL.embed([text])))
            if isinstance(vec, np.ndarray):
                return vec.tolist()
            return list(vec)
        if _SENTENCE_MODEL is not None:
            # sentence-transformers returns a numpy array, convert to standard python float list
            import numpy as np

            vec = _SENTENCE_MODEL.encode(text)
            if isinstance(vec, np.ndarray):
                return vec.tolist()
            return list(vec)
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
        mag = math.sqrt(sum(v * v for v in vec))
        if mag > 0:
            vec = [v / mag for v in vec]

        return vec
