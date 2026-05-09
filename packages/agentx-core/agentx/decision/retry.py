"""
agentx/decision/retry.py
========================
Phase 10 — Decision Retry Controller.
Phase 12 — Causal Rule Integration.
Phase 14 — Metric-Scored Strategy Selection & Convergence.

Retries tasks based on evaluation results.
Strategy transitions are now scored by historical metrics rather than
a fixed ladder, and strategies below BLOCK_THRESHOLD are excluded.
"""

import logging
import time
import hashlib
from typing import Dict, Any, Tuple, Optional, List

logger = logging.getLogger("agentx.decision.retry")

MAX_RETRIES = 3

# Map causal rule actions → internal retry actions
_RULE_ACTION_MAP = {
    "ASK":               "CHANGE_STRATEGY",
    "REJECT":            "CHANGE_STRATEGY",
    "RETRY":             "RETRY_REFINE",
    "RETRY_WITH_DELAY":  "RETRY_REFINE",
    "SWITCH_STRATEGY":   "CHANGE_STRATEGY",
}


def retry_strategy(
    decision: Dict[str, Any],
    evaluation: str,
    attempt: int,
    last_result_hash: str,
    current_result_str: str,
    error: str = "",
    result_text: str = "",
) -> Tuple[str, Dict[str, Any], str]:
    """
    Determine the next action based on the evaluation outcome.

    Priority:
        1. TRUE_SUCCESS           → STOP
        2. Causal rule match      → targeted corrective action
        3. Metric-scored ladder   → best available next strategy
        4. Default fallback       → ASK

    Returns
    -------
    (action, new_decision, new_result_hash)
        action: "STOP" | "RETRY_REFINE" | "CHANGE_STRATEGY" | "FAIL"
    """
    # 1. Goal reached
    if evaluation == "TRUE_SUCCESS":
        return "STOP", decision, ""

    current_hash = _hash(current_result_str)
    no_progress = (current_hash == last_result_hash) and bool(current_hash)

    # 2. Hard limit
    if attempt >= MAX_RETRIES:
        logger.warning("[Retry] Max retries (%d) reached. Escalating.", MAX_RETRIES)
        decision["type"] = "ASK"
        decision["reason"] = "Max retries reached without true success."
        return "FAIL", decision, current_hash

    # 3. Causal rule check (Phase 12)
    if error or evaluation in ("FALSE_SUCCESS", "FAILURE"):
        causal_action = _lookup_causal_rule(error, result_text)
        if causal_action:
            return _apply_causal_action(causal_action, decision, current_hash, error)

    # 4. PARTIAL_SUCCESS with progress → refine same strategy
    if evaluation == "PARTIAL_SUCCESS" and not no_progress:
        logger.info("[Retry] Attempt %d → PARTIAL_SUCCESS with progress. Refining.", attempt)
        return "RETRY_REFINE", decision, current_hash

    # 5. Metric-scored strategy change (Phase 14)
    if no_progress:
        logger.warning("[Retry] NO_PROGRESS detected (same output hash). Changing strategy.")

    next_type = _pick_next_strategy(decision.get("type", "NEW"))
    decision["type"] = next_type
    decision["reason"] = f"Strategy changed to {next_type} (metric-scored)."
    logger.info("[Retry] RETRY_STRATEGY_CHANGED → %s", next_type)

    return "CHANGE_STRATEGY", decision, current_hash


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest() if text else ""


def _pick_next_strategy(current_type: str) -> str:
    """Use convergence module to score the next strategy from live metrics."""
    try:
        from agentx.decision.metrics import get_metrics
        from agentx.decision.convergence import score_strategies, get_blocked_strategies

        metrics_data = get_metrics()
        blocked = get_blocked_strategies(metrics_data)

        if blocked:
            logger.info("[Retry] STRATEGY_BLOCKED: %s", blocked)
            for b in blocked:
                print(f"[Retry] STRATEGY_BLOCKED: {b} (accuracy below threshold)")

        next_type = score_strategies(current_type, metrics_data, blocked)
        return next_type
    except Exception as e:
        logger.debug("[Retry] Metric scoring failed, using fixed ladder: %s", e)
        # Fallback fixed ladder
        ladder = {"SKILL": "COMPOSE", "COMPOSE": "NEW", "NEW": "ASK"}
        return ladder.get(current_type, "ASK")


def _lookup_causal_rule(error: str, result_text: str) -> Optional[str]:
    """Classify the error and return the causal action, or None."""
    try:
        from agentx.decision.rules import classify_failure, check_rules_for_failure
        condition_type = classify_failure(error, result_text)
        if condition_type == "GENERAL":
            return None
        action = check_rules_for_failure(condition_type)
        if action:
            logger.info("[Retry] RULE_APPLIED_CAUSAL: %s → %s", condition_type, action)
            print(f"[Retry] RULE_APPLIED_CAUSAL: {condition_type} → {action}")
        return action
    except Exception as e:
        logger.debug("[Retry] Causal rule lookup failed: %s", e)
        return None


def _apply_causal_action(
    causal_action: str,
    decision: Dict[str, Any],
    current_hash: str,
    error: str,
) -> Tuple[str, Dict[str, Any], str]:
    """Translate a causal action string into a (retry_action, decision, hash) tuple."""
    causal_upper = causal_action.upper()

    if causal_upper == "ASK":
        decision["type"] = "ASK"
        decision["reason"] = f"Causal rule: failure classified → escalating to human."
        return "CHANGE_STRATEGY", decision, current_hash

    if causal_upper == "REJECT":
        decision["type"] = "REJECT"
        decision["reason"] = f"Causal rule: unrecoverable failure → REJECT ({error[:80]})."
        return "CHANGE_STRATEGY", decision, current_hash

    if causal_upper in ("RETRY", "RETRY_WITH_DELAY"):
        decision["reason"] = f"Causal rule: retrying with appropriate delay."
        return "RETRY_REFINE", decision, current_hash

    if causal_upper == "SWITCH_STRATEGY":
        decision["type"] = _pick_next_strategy(decision.get("type", "NEW"))
        decision["reason"] = f"Causal rule: switching strategy."
        return "CHANGE_STRATEGY", decision, current_hash

    return "RETRY_REFINE", decision, current_hash


def apply_backoff(attempt: int):
    """Apply exponential backoff: 1 s → 2 s → 4 s …"""
    if attempt > 0:
        delay = 2 ** (attempt - 1)
        logger.info("[Retry] Backing off %d s before next attempt.", delay)
        time.sleep(delay)
