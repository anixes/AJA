"""agentx/decision/context.py
================================
Phase 16 — Context Compaction (Anti-Drift).

compact_context() strips a decision context dict down to the minimal set
of signals needed for the next retry, discarding stale retry logs,
repeated outputs, and redundant state that could cause the LLM to drift.

No LLM calls. Pure dict transformation.
Logs: CONTEXT_COMPACTED
"""

import logging
from typing import Any, Dict

logger = logging.getLogger("agentx.decision.context")

# Keys that are always preserved verbatim
_KEEP_KEYS = frozenset({
    "objective",
    "last_decision",
    "last_result",
    "last_error",
    "system_state",
    "risk_level",
    "top_skills",       # small list, decision-critical
    "task_id",
    "task_status",
})

# Keys that grow unboundedly and should be dropped on compact
_DROP_KEYS = frozenset({
    "task_history",         # full run history — can be huge
    "feedback_stats",       # stale per-attempt stats
    "similar_decisions",    # already used; re-fetched next decide()
    "metrics_summary",      # text blob; re-built next decide()
    "metrics_data",         # raw dict; re-fetched next decide()
    "hash_history",         # managed by retry loop, not decision context
    "outcome_history",      # same
})

# Maximum character length for string values (prevents bloat from large outputs)
_MAX_VALUE_LEN = 512


def compact_context(context: Dict[str, Any], tracker=None) -> Dict[str, Any]:
    """
    Return a compacted copy of the context dict, keeping only the signals
    that are stable and decision-relevant.

    Compaction rules:
        1. Preserve all keys in _KEEP_KEYS.
        2. Drop all keys in _DROP_KEYS.
        3. Truncate any string value that exceeds _MAX_VALUE_LEN.
        4. Drop any key whose value is an empty list or empty dict.
        5. Unknown keys not in either set are preserved (conservative).

    Logs CONTEXT_COMPACTED with before/after key counts.
    """
    before = len(context)
    compacted: Dict[str, Any] = {}

    for key, value in context.items():
        if key in _DROP_KEYS:
            continue
        # Truncate oversized strings (last_result, last_error)
        if isinstance(value, str) and len(value) > _MAX_VALUE_LEN:
            value = value[:_MAX_VALUE_LEN] + "...[truncated]"
        # Drop empty containers
        if isinstance(value, (list, dict)) and not value:
            continue
        compacted[key] = value

    after = len(compacted)
    dropped = before - after

    if dropped > 0:
        logger.info(
            "[Context] CONTEXT_COMPACTED: %d keys -> %d keys (dropped %d)",
            before, after, dropped
        )
        print(f"[Context] CONTEXT_COMPACTED: {before} -> {after} keys (dropped {dropped})")
        if tracker:
            try:
                tracker.log_event("CONTEXT_COMPACTED", {
                    "before": before,
                    "after": after,
                    "dropped": dropped,
                })
            except Exception:
                pass

    return compacted
