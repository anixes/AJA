"""agentx/decision/convergence.py
================================
Phase 14 — Loop Stability & Convergence Control.
Phase 16 — Confidence-aware convergence.

Provides pure, stateless helper functions to:
  1. Detect output stagnation (same hash repeated)
  2. Detect no-improvement across attempts
  3. Signal goal satisfaction (with confidence gate)
  4. Score next strategy using historical metrics
  5. Block strategies below a hard accuracy threshold (STRATEGY_BLOCKED_HARD)

All functions are deterministic. No LLM calls.
"""

import hashlib
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("agentx.decision.convergence")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How many identical consecutive output hashes = stagnation
STAGNATION_WINDOW = 2

# Minimum historical accuracy (0.0-1.0) before a strategy is hard-blocked
BLOCK_THRESHOLD = 0.25

# Minimum samples required before blocking a strategy
MIN_SAMPLES_TO_BLOCK = 5

# Phase 16: confidence must be >= this to accept TRUE_SUCCESS as convergence.
# Below this, convergence is treated as uncertain and escalated to ASK.
CONFIDENCE_SAFE_THRESHOLD = 0.70

# Strategy priority ladder (used when metrics are insufficient)
_DEFAULT_LADDER = ["SKILL", "COMPOSE", "NEW", "ASK"]


# ---------------------------------------------------------------------------
# Hash utilities
# ---------------------------------------------------------------------------

def output_hash(text: str) -> str:
    """MD5 of normalised output text."""
    return hashlib.md5(text.strip().encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# Stagnation & repetition detection
# ---------------------------------------------------------------------------

def detect_stagnation(hash_history: List[str]) -> bool:
    """
    Return True if the last STAGNATION_WINDOW hashes are identical and non-empty.

    Parameters
    ----------
    hash_history : ordered list of output hashes, oldest first
    """
    if len(hash_history) < STAGNATION_WINDOW:
        return False
    recent = hash_history[-STAGNATION_WINDOW:]
    return len(set(recent)) == 1 and recent[0] != ""


def detect_no_improvement(outcome_history: List[str]) -> bool:
    """
    Return True if no TRUE_SUCCESS has appeared in the outcome history and
    the last two outcomes were not PARTIAL_SUCCESS (meaning we're not even
    making incremental progress).
    """
    if not outcome_history:
        return False
    if "TRUE_SUCCESS" in outcome_history:
        return False
    last_two = outcome_history[-2:]
    all_partial = all(o in ("PARTIAL_SUCCESS",) for o in last_two)
    return len(last_two) >= 2 and not all_partial


# ---------------------------------------------------------------------------
# Goal satisfaction — Phase 16: confidence-aware
# ---------------------------------------------------------------------------

def is_goal_satisfied(outcome: str, _result_text: str = "",
                      confidence: float = 1.0,
                      task_uncertainty: float = 0.0) -> str:
    """
    Return a convergence signal string:

        "STOP"      — outcome is TRUE_SUCCESS and confidence is safe
        "ESCALATE"  — outcome is TRUE_SUCCESS but confidence is too low or uncertainty is too high
        "CONTINUE"  — outcome is not TRUE_SUCCESS; keep retrying

    Intentionally conservative: PARTIAL_SUCCESS always returns CONTINUE.

    Phase 16: confidence < CONFIDENCE_SAFE_THRESHOLD forces ESCALATE rather
    than STOP to prevent premature convergence on uncertain evaluations.
    Logs: CONVERGENCE_LOW_CONFIDENCE
    """
    if outcome != "TRUE_SUCCESS":
        return "CONTINUE"
    if confidence < CONFIDENCE_SAFE_THRESHOLD:
        logger.warning(
            "[Convergence] CONVERGENCE_LOW_CONFIDENCE: confidence=%.2f < %.2f — escalating",
            confidence, CONFIDENCE_SAFE_THRESHOLD
        )
        print(
            f"[Convergence] CONVERGENCE_LOW_CONFIDENCE: {confidence:.2f} "
            f"< {CONFIDENCE_SAFE_THRESHOLD} -> ESCALATE"
        )
        return "ESCALATE"
    
    # Phase 21: Overriding convergence with accumulated uncertainty
    if task_uncertainty > 0.5:
        logger.warning(
            "[Convergence] CONVERGENCE_HIGH_UNCERTAINTY: uncertainty=%.2f — escalating",
            task_uncertainty
        )
        print(
            f"[Convergence] CONVERGENCE_HIGH_UNCERTAINTY: {task_uncertainty:.2f} "
            f"-> ESCALATE"
        )
        return "ESCALATE"
        
    return "STOP"


# ---------------------------------------------------------------------------
# Metric-scored next strategy
# ---------------------------------------------------------------------------

def score_strategies(
    current_type: str,
    metrics_data: Dict[str, Any],
    blocked: Optional[List[str]] = None,
) -> str:
    """
    Pick the best next strategy using historical accuracy scores.

    Scoring:
        - Prefer the strategy with the highest accuracy (min 5 samples).
        - Skip blocked strategies.
        - Fall back to _DEFAULT_LADDER order if metrics are sparse.

    Parameters
    ----------
    current_type    : strategy that just failed
    metrics_data    : dict from metrics.get_metrics()
    blocked         : list of strategy names to exclude

    Returns
    -------
    Next strategy type string.
    """
    blocked = set(blocked or [])
    blocked.add(current_type)  # never repeat the one that just failed

    per_type = metrics_data.get("per_type", {})
    candidates = []

    for strategy in _DEFAULT_LADDER:
        if strategy in blocked:
            continue
        stats = per_type.get(strategy, {})
        total = stats.get("total", 0)
        accuracy = stats.get("accuracy", 0.5) if total >= MIN_SAMPLES_TO_BLOCK else 0.5
        candidates.append((accuracy, strategy))

    if not candidates:
        return "ASK"  # hard fallback

    # highest accuracy first; if tied, prefer earlier in DEFAULT_LADDER
    candidates.sort(key=lambda x: (-x[0], _DEFAULT_LADDER.index(x[1])))
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Hard metric thresholds — Phase 16: STRATEGY_BLOCKED_HARD
# ---------------------------------------------------------------------------

def get_blocked_strategies(metrics_data: Dict[str, Any]) -> List[str]:
    """
    Return a list of strategies whose historical accuracy is below
    BLOCK_THRESHOLD with enough data (MIN_SAMPLES_TO_BLOCK).

    These strategies will be skipped by score_strategies().
    Logs: STRATEGY_BLOCKED_HARD
    """
    blocked = []
    per_type = metrics_data.get("per_type", {})
    for strategy, stats in per_type.items():
        if stats.get("total", 0) >= MIN_SAMPLES_TO_BLOCK:
            if stats.get("accuracy", 1.0) < BLOCK_THRESHOLD:
                blocked.append(strategy)
                logger.warning(
                    "[Convergence] STRATEGY_BLOCKED_HARD: %s accuracy=%.0f%% "
                    "(threshold=%.0f%%) n=%d",
                    strategy, stats["accuracy"] * 100,
                    BLOCK_THRESHOLD * 100, stats["total"]
                )
                print(
                    f"[Convergence] STRATEGY_BLOCKED_HARD: {strategy} "
                    f"accuracy={int(stats['accuracy']*100)}% n={stats['total']}"
                )
    return blocked

# ---------------------------------------------------------------------------
# Final Gap: Verify Convergence (Phase 16 -> 11)
# ---------------------------------------------------------------------------

def verify_convergence(task_id: int, result: str, context: Dict[str, Any], confidence: float = 1.0) -> str:
    """
    Run the production-grade multi-evaluator pipeline to verify convergence correctness.
    
    Cost Optimization:
        Only run strict verification if:
        - High-risk task (flag in context)
        - Low confidence (e.g. < 0.8)
        
    Returns:
        "VERIFIED" if the pipeline returns PASS with low risk
        "ESCALATE" if convergence is detected but risk is high
        "UNCERTAIN" otherwise
    """
    try:
        # Phase 17 Cost Optimization
        is_high_risk = context.get("high_risk", False)
        # Skip multi-eval if it's low risk and we have extremely high confidence
        if not is_high_risk and confidence >= 0.85:
            logger.info(f"[Convergence] Skipping strict verification (high confidence + low risk) for task {task_id}")
            return "VERIFIED"

        from agentx.decision.evaluator import evaluate_pipeline
        from agentx.decision.metrics import get_metrics
        
        verification_result = evaluate_pipeline(task_id, result, context, stricter=True, confidence=confidence)
        
        metrics = get_metrics()
        RISK_THRESHOLD = metrics.get("dynamic_risk_threshold", 0.5)
        decision = verification_result.get("decision", "UNCERTAIN")
        risk_score = verification_result.get("risk_score", 1.0)
        
        if decision == "PASS":
            if risk_score > RISK_THRESHOLD:
                logger.warning(f"[Convergence] HIGH_RISK_CONVERGENCE for task {task_id} (Risk: {risk_score} > Threshold: {RISK_THRESHOLD})")
                print(f"[Convergence] Escalating high-risk convergence. Risk={risk_score}")
                return "ESCALATE"
                
            logger.info(f"[Convergence] CONVERGENCE_VERIFIED for task {task_id}")
            return "VERIFIED"
        else:
            logger.warning(
                f"[Convergence] Verification mismatch for task {task_id}: {verification_result}"
            )
            return "UNCERTAIN"
            
    except Exception as e:
        logger.error(f"[Convergence] Convergence verification failed: {e}")
        return "UNCERTAIN"
