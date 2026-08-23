"""
Per-provider/model-family token estimation.

The Rust tokenizer (aja_native) is exact for cl100k families (OpenAI, Copilot)
but wrong by 10-30% for other model families (Gemini, Llama/Gemma/Qwen
sentencepiece variants). This module maps provider/model to the right
estimation strategy so context-window decisions stay honest across
cloud + local dual-model setups.
"""

from typing import Any, Dict, List, Optional

# chars-per-token heuristics per family (empirical midpoints; ±15% typical).
_FAMILY_CHARS_PER_TOKEN = {
    "cl100k": 3.9,        # OpenAI/Copilot — also the aja_native exact path
    "gemini": 4.0,        # SentencePiece-ish; empirical Google guidance ~4 ch/tok
    "llama": 3.5,         # Llama/Qwen/Mistral sentencepiece BPE denser on code
    "gemma": 4.0,
}

_CLOUD_PROVIDERS = {"google", "openai", "openrouter", "copilot", "anthropic"}
_LOCAL_PROVIDERS = {"llama_cpp", "ollama"}

_LOCAL_MODEL_HINTS = {
    "qwen": "llama",
    "mistral": "llama",
    "llama": "llama",
    "deepseek": "llama",
    "phi": "llama",
    "gemma": "gemma",
}


def tokenizer_family(provider: str = "", model: str = "") -> str:
    """Maps (provider, model) to an estimation family."""
    provider_l = (provider or "").lower()
    model_l = (model or "").lower()

    if provider_l in _LOCAL_PROVIDERS:
        for hint, family in _LOCAL_MODEL_HINTS.items():
            if hint in model_l:
                return family
        return "llama"  # local servers overwhelmingly host llama-family models

    if provider_l == "anthropic":
        return "cl100k"  # Claude's tokenizer tracks cl100k density closely
    if provider_l in ("google",):
        return "gemini"
    return "cl100k"


def _native_count(text: str) -> Optional[int]:
    """Exact cl100k count via the Rust extension; None when unavailable."""
    try:
        from aja import aja_native

        if hasattr(aja_native, "count_tokens"):
            return int(aja_native.count_tokens(text))
    except Exception:
        pass
    return None


def estimate_tokens(text: str, provider: str = "", model: str = "") -> int:
    """
    Estimates token count for text under the given provider/model.

    - cl100k family: exact via aja_native (Rust), Python char-heuristic fallback
    - other families: chars-per-token heuristic
    """
    family = tokenizer_family(provider, model)
    if not text:
        return 0
    if family == "cl100k":
        exact = _native_count(text)
        if exact is not None:
            return exact
    chars_per_tok = _FAMILY_CHARS_PER_TOKEN[family]
    return max(1, round(len(text) / chars_per_tok))


def estimate_messages_tokens(
    messages: List[Dict[str, Any]],
    provider: str = "",
    model: str = "",
    overhead_per_message: int = 4,
) -> int:
    """
    Estimates total tokens for a chat message list, including per-message
    framing overhead (role markers etc.).
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, str):
            # Multimodal/content-block shapes: stringify text blocks only.
            try:
                content = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            except TypeError:
                content = str(content)
        total += estimate_tokens(content, provider, model) + overhead_per_message
    return total
