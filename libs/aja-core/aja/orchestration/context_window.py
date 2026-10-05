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

import json
import os
from typing import List, Dict, Optional, Any

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
    # Copilot (routes to underlying Claude / GPT; strict prompt token cap of 12,288)
    "copilot":            12_288,
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
    model_needle = (model or "").lower()
    provider_needle = (provider or "").lower()
    is_copilot = "copilot" in provider_needle or "copilot" in model_needle

    limit = None
    for key, cap in _MODEL_LIMITS.items():
        if key in model_needle:
            limit = cap
            break

    if limit is None and is_copilot:
        limit = _MODEL_LIMITS["copilot"]
    elif limit is None:
        for key, cap in _MODEL_LIMITS.items():
            if key in provider_needle:
                limit = cap
                break

    if limit is None:
        limit = _DEFAULT_LIMIT

    if is_copilot:
        limit = min(limit, _MODEL_LIMITS["copilot"])

    return int(limit * _BUDGET_FRACTION)


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


def _pop_leading_tool_messages(messages: List[Dict[str, Any]], start_idx: int = 1) -> int:
    """Pop consecutive role='tool' messages at start_idx to maintain valid turn pairing."""
    dropped = 0
    while len(messages) > start_idx + 1 and messages[start_idx].get("role") == "tool":
        messages.pop(start_idx)
        dropped += 1
    return dropped


def atomic_prune_messages(
    messages: List[Dict[str, Any]], target_drops: int = 1, preserve_first: int = 1
) -> int:
    """Safely drop at least `target_drops` older turns/steps starting after `preserve_first`,
    preserving messages[:preserve_first] (e.g. initial prompt / objective, or system + user task).

    Guarantees that an assistant message with `tool_calls` and all its
    corresponding `role == 'tool'` response messages are pruned together
    atomically, never leaving orphaned tool_calls or role='tool' messages.
    Also defensively cleans up any orphaned tool responses at index preserve_first.

    Returns the number of messages dropped.
    """
    if not isinstance(messages, list) or len(messages) <= preserve_first + 1:
        return 0

    dropped = 0
    while len(messages) > preserve_first + 1 and dropped < target_drops:
        messages.pop(preserve_first)
        dropped += 1
        dropped += _pop_leading_tool_messages(messages, start_idx=preserve_first)

    # Defensive final sweep: ensure index preserve_first is not an orphaned role="tool"
    dropped += _pop_leading_tool_messages(messages, start_idx=preserve_first)

    return dropped


def compress_history(
    history: List[dict],
    model: str = "",
    provider: str = "",
    reserve_tokens: int = 2_048,
    system_prompt: str = "",
    tools: Optional[List[dict]] = None,
    **kwargs: Any,
) -> None:
    """Slide the rolling window on *history* (mutated **in-place**) so the
    estimated total token count stays within the model's safe budget.

    Strategy:
    - Always preserve the **first message** (it contains the task objective).
    - Atomically drop older turns from index 1 forward.
    - If `system_prompt` and `tools` are passed, calculate explicit overhead
      so tool schemas (~5,000 tokens) do not blow past provider limits.
    - For small context models like Copilot (12,288 total cap), reserve adequate
      headroom so multi-turn sessions never cause prompt overflow.
    - Stop when only 2 messages remain (first + last), regardless of budget.

    Args:
        history:        The shared session_history list (mutated in-place).
        model:          Lowercase model name string for limit resolution.
        provider:       Lowercase provider name string (fallback for limit).
        reserve_tokens: Tokens to hold back for system prompt + response.
        system_prompt:  Optional system prompt text to compute baseline overhead.
        tools:          Optional tool schema list to compute tool token overhead.
    """
    if len(history) <= 2:
        return

    is_copilot = "copilot" in (provider or "").lower() or "copilot" in (model or "").lower()

    # Calculate explicit overhead from system prompt and tool schemas if provided
    overhead = 0
    if system_prompt:
        overhead += estimate_tokens(system_prompt)
    if tools:
        try:
            overhead += estimate_tokens(json.dumps(tools))
        except Exception:
            pass

    # If overhead not explicitly provided, apply realistic reserve for models with small windows
    if overhead == 0 and is_copilot:
        # Copilot has a strict 12,288 total token cap. Native tool schemas (~5,100 tokens)
        # plus standard system prompt (~1,800 tokens) consume ~6,900 tokens.
        effective_reserve = max(reserve_tokens, 6_500)
    else:
        effective_reserve = reserve_tokens + overhead

    raw_limit = resolve_model_limit(model, provider)
    limit = raw_limit - effective_reserve
    # History floor: guarantee enough budget for multi-turn history even with heavy tool/prompt overhead
    history_floor = 2_500 if is_copilot else 4_096
    if limit < history_floor:
        limit = max(history_floor, int(raw_limit * 0.35))

    def _total_tokens() -> int:
        total = 0
        for msg in history:
            total += estimate_tokens(str(msg.get("content") or ""))
            if msg.get("tool_calls"):
                total += estimate_tokens(str(msg.get("tool_calls")))
        return total

    while _total_tokens() > limit and len(history) > 2:
        atomic_prune_messages(history, target_drops=1)
