"""
context_window.py — Context Window Management for AJA DirectSession
====================================================================
Provides three utilities for keeping shared session_history safely within
the active LLM's token limit:

  1. estimate_tokens(text)            — fast character-based heuristic
  2. truncate_tool_result(raw, max)   — head+tail truncation with clear marker
  3. compress_history(history, ...)   — sliding-window trim on the shared list

No heavy dependencies (no tiktoken, no sentencepiece required).
Token estimates are deliberately conservative (~3.5 chars/token).
"""

from __future__ import annotations

import os
from typing import List, Dict

# Lazy-import AJA config at module level so tests can patch `CONFIG` directly.
# Silently set to None if unavailable (test isolation, fresh installs, etc.).
try:
    from aja.config import CONFIG as CONFIG
except Exception:
    CONFIG = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maximum characters kept per tool result before head+tail truncation.
# Overridable at runtime via AJA_MAX_TOOL_RESULT_CHARS env var.
MAX_TOOL_RESULT_CHARS: int = int(
    os.environ.get("AJA_MAX_TOOL_RESULT_CHARS", "8000")
)

# How many lines to keep at the head and tail of a truncated tool result.
_HEAD_LINES: int = 40
_TAIL_LINES: int = 40

# Characters-per-token estimate (conservative; real ratio is ~4 for English).
_CHARS_PER_TOKEN: float = 3.5

# Safety ceiling — never use more than this fraction of the model's token limit.
_BUDGET_FRACTION: float = 0.80

# Known model context windows (in tokens).  Keys are lowercased model-name
# substrings; matched in order — first hit wins.
_MODEL_LIMITS: Dict[str, int] = {
    # Anthropic / Claude
    "claude-3-5-sonnet":  200_000,
    "claude-3-7-sonnet":  200_000,
    "claude-3-5-haiku":   200_000,
    "claude-haiku":       200_000,
    "claude-sonnet":      200_000,
    "claude-opus":        200_000,
    "claude":             200_000,
    # OpenAI
    "gpt-4o":             128_000,
    "gpt-4-turbo":        128_000,
    "gpt-4":               8_192,
    "gpt-3.5":            16_385,
    "o1":                 200_000,
    # Google / Gemini
    "gemini-2.5":       1_000_000,
    "gemini-2.0":       1_000_000,
    "gemini-1.5":       1_000_000,
    "gemini-1.0":         32_768,
    "gemini":             32_768,
    # Local / llama
    "llama-3":             8_192,
    "llama":               4_096,
    "gemma":               8_192,
    # Copilot (routes to underlying Claude / GPT; use conservative Claude cap)
    "copilot":           128_000,
}

# Default when model is unknown.
_DEFAULT_LIMIT: int = 12_288


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """
    Conservative heuristic token count for *text*.

    Uses character-count / 3.5.  No external libraries required.
    Deliberately over-estimates to stay safely under provider limits.
    """
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def resolve_model_limit(model: str = "", provider: str = "") -> int:
    """
    Determine the conservative token budget for *model* / *provider*.

    Resolution order (first win):

    1. **aja.json override** — ``swarm_settings.context_limit_tokens``
       Set this in your ``aja.json`` to use the real limit for any model,
       including custom, fine-tuned, or yet-to-be-released ones::

           "swarm_settings": {"context_limit_tokens": 1000000}

    2. **Built-in lookup table** — ``_MODEL_LIMITS`` substring match on the
       lowercased model / provider string.  Updated periodically; not
       exhaustive.

    3. **Safe default floor** — ``_DEFAULT_LIMIT`` (12,288 tokens), used
       when neither of the above matches.

    The ``_BUDGET_FRACTION`` safety ceiling (80%) is applied in all cases
    to leave headroom for the system prompt and the model's next reply.
    """
    # --- Tier 1: aja.json explicit override ---------------------------------
    try:
        limit_override = getattr(
            getattr(CONFIG, "swarm_settings", None),
            "context_limit_tokens",
            None,
        )
        if limit_override and limit_override > 0:
            return int(limit_override * _BUDGET_FRACTION)
    except Exception:
        pass  # CONFIG unavailable or misconfigured — continue to next tier

    # --- Tier 2: Built-in lookup table --------------------------------------
    needle = (model or provider or "").lower()
    for key, limit in _MODEL_LIMITS.items():
        if key in needle:
            return int(limit * _BUDGET_FRACTION)

    # --- Tier 3: Safe default floor -----------------------------------------
    return int(_DEFAULT_LIMIT * _BUDGET_FRACTION)


def truncate_tool_result(raw: str, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """
    Truncate *raw* tool output to at most *max_chars* characters.

    Short outputs (≤ max_chars) are returned unchanged.
    Long outputs are replaced by:
        <first HEAD_LINES lines>
        [... truncated X chars — showing first/last N lines only ...]
        <last TAIL_LINES lines>

    This ensures the LLM still sees useful context (the beginning of a file
    listing or the end of a shell trace) without poisoning the token budget.
    """
    if len(raw) <= max_chars:
        return raw

    lines = raw.splitlines()
    total = len(lines)

    if total <= _HEAD_LINES + _TAIL_LINES:
        # Enough lines to show all; just cap by chars
        return raw[:max_chars] + f"\n[... truncated at {max_chars} chars ...]"

    head = lines[:_HEAD_LINES]
    tail = lines[-_TAIL_LINES:]
    skipped = total - _HEAD_LINES - _TAIL_LINES
    marker = (
        f"\n[... {skipped} lines ({len(raw):,} chars total) truncated — "
        f"showing first {_HEAD_LINES} and last {_TAIL_LINES} lines only ...]\n"
    )
    truncated = "\n".join(head) + marker + "\n".join(tail)

    # Final safety cap in case head+tail themselves are huge
    if len(truncated) > max_chars * 2:
        truncated = truncated[: max_chars * 2] + "\n[... further truncated ...]"

    return truncated


def compress_history(
    history: List[dict],
    model: str = "",
    provider: str = "",
    reserve_tokens: int = 2_048,
) -> None:
    """
    Slide the rolling window on *history* (mutated **in-place**) so the
    estimated total token count stays within the model's safe budget.

    Strategy:
    - Always preserve the **first message** (it often contains the original
      task objective which anchors the whole session).
    - Drop the **second-oldest** message on each iteration until we're safe.
    - Stop when only 2 messages remain (first + last), regardless of budget.

    Args:
        history:        The shared session_history list (mutated in-place).
        model:          Lowercase model name string for limit resolution.
        provider:       Lowercase provider name string (fallback for limit).
        reserve_tokens: Tokens to hold back for system prompt + response.
    """
    if len(history) <= 2:
        return

    limit = resolve_model_limit(model, provider) - reserve_tokens
    if limit <= 0:
        limit = max(1024, int(_DEFAULT_LIMIT * _BUDGET_FRACTION))

    def _total_tokens() -> int:
        return sum(
            estimate_tokens(str(msg.get("content", "")))
            for msg in history
        )

    while _total_tokens() > limit and len(history) > 2:
        # Drop the second-oldest message (index 1) to preserve the first one
        history.pop(1)
