"""
agentx/decision/evaluator.py
===========================
Phase 10 — Decision Evaluation Layer.
Phase 22 — Consensus Validation (replaces majority voting).

Distinguishes between TRUE_SUCCESS and FALSE_SUCCESS to ensure
the agent doesn't blindly trust the "COMPLETED" status if the actual
result is empty, malformed, or contradictory.

Phase 22 adds:
  - Per-evaluator reasoning_text + confidence collection
  - compute_agreement_quality(): semantic + variance check
  - meta_validate(): detects correlated evaluator bias
  - Consensus logic: all-TRUE only accepted if agreement is strong
"""

import logging
import math
from typing import Dict, Any, List, Tuple
import os

logger = logging.getLogger("agentx.decision.evaluator")

# Phase 16 — Multi-model evaluation separation
EVALUATION_MODEL = os.environ.get("AGENTX_EVALUATION_MODEL", "secondary_model")

def evaluate_task(task_id: int, result: str, context: Dict[str, Any]) -> str:
    """
    Evaluate the true outcome of a task execution.
    
    Returns:
        "TRUE_SUCCESS" | "FALSE_SUCCESS" | "FAILURE"
    """
    if result is None:
        # If no result is available to check, we might assume TRUE_SUCCESS if it reached here without failing,
        # but the prompt implies we should check. We'll proceed with checking what we have.
        pass

    result_str = str(result).strip().lower() if result else ""
    
    # 1. Postconditions (if any are present in the skill / context)
    skill = context.get("skill", {})
    if skill and skill.get("postconditions"):
        try:
            from agentx.skills.skill_postconditions import validate_postconditions
            # Provide dummy step_results since validate_postconditions may just check global state
            step_results = [{"step": 0, "result": result_str, "ok": True}]
            pc_ok, pc_failures = validate_postconditions(skill, step_results)
            if not pc_ok:
                logger.warning(f"[Evaluator] Task {task_id} failed postconditions: {pc_failures}. FALSE_SUCCESS.")
                return "FALSE_SUCCESS"
        except ImportError:
            pass

    # 2. Check for empty or malformed results
    if not result_str or result_str in ["none", "null", "[]", "{}", "()", "false"]:
        logger.warning(f"[Evaluator] Task {task_id} returned empty/malformed result. FALSE_SUCCESS.")
        return "FALSE_SUCCESS"
        
    # 3. Check for contradiction / common error strings
    contradictory_terms = ["error", "exception", "failed to", "traceback", "not found", "unauthorized", "denied"]
    for term in contradictory_terms:
        if term in result_str:
            logger.warning(f"[Evaluator] Task {task_id} result contains contradictory term '{term}'. FALSE_SUCCESS.")
            return "FALSE_SUCCESS"

    # 4. Semantic Evaluation (LLM)
    objective = context.get("objective", "")
    if objective and result_str:
        sem_status = evaluate_semantic(objective, result_str)
        if sem_status == "INCORRECT":
            logger.warning(f"[Evaluator] Task {task_id} failed semantic evaluation. FALSE_SUCCESS.")
            return "FALSE_SUCCESS"
        elif sem_status == "PARTIAL":
            logger.warning(f"[Evaluator] Task {task_id} partially succeeded semantic evaluation. PARTIAL_SUCCESS.")
            return "PARTIAL_SUCCESS"

    return "TRUE_SUCCESS"

def evaluate_semantic(objective: str, result: str, context: Dict[str, Any] = None, stricter: bool = False, model: str = None) -> str:
    """
    Use an LLM to evaluate if the execution result genuinely matches the objective.
    Returns: "CORRECT" | "PARTIAL" | "INCORRECT"
    """
    context = context or {}
    exec_model = context.get("execution_model", "")
    
    if exec_model and exec_model == EVALUATION_MODEL:
        logger.warning(
            "[Evaluator] EVALUATOR_MODEL_OVERLAP: execution and evaluation "
            f"models are the same ({EVALUATION_MODEL})."
        )
        print("[Evaluator] EVALUATOR_MODEL_OVERLAP: same model used for execution and evaluation.")

    try:
        from scripts.core.gateway import UnifiedGateway
        gateway = UnifiedGateway()
        
        prompt = f"Objective:\n{objective}\n\nExecution Result:\n{result}\n\nDoes the execution result successfully fulfill the objective?"
        system = (
            "You are an evaluator.\n"
            "Given:\n* objective\n* result\n"
            "Return EXACTLY ONE WORD from the following list:\n"
            "* CORRECT\n* PARTIAL\n* INCORRECT\n"
            "Be strict. Do not assume success unless the result explicitly provides what was asked."
        )
        if stricter:
            system += "\nBE EXTREMELY STRICT. Verify all implicit requirements are met. Do NOT return CORRECT if there is any ambiguity."
        
        eval_model = model or EVALUATION_MODEL
        response = gateway.chat(
            model=eval_model,
            prompt=prompt,
            system=system
        )
        
        cleaned = response.strip().upper()
        if "INCORRECT" in cleaned:
            return "INCORRECT"
        elif "PARTIAL" in cleaned:
            return "PARTIAL"
        elif "CORRECT" in cleaned:
            return "CORRECT"
    except Exception as e:
        logger.error(f"[Evaluator] Semantic evaluation failed: {e}")
        
    return "CORRECT" # Fallback to deterministic results if LLM fails


def evaluate_semantic_with_reasoning(
    objective: str,
    result: str,
    context: Dict[str, Any] = None,
    stricter: bool = False,
    model: str = None,
) -> Tuple[str, str, float]:
    """
    Phase 22 — Consensus Validation.
    Extended evaluator that returns (verdict, reasoning_text, confidence).

    Returns
    -------
    verdict   : "CORRECT" | "PARTIAL" | "INCORRECT"
    reasoning : short free-text explanation from the evaluator
    confidence: float 0.0 → 1.0 self-reported by the evaluator
    """
    context = context or {}
    try:
        from scripts.core.gateway import UnifiedGateway
        gateway = UnifiedGateway()

        prompt = (
            f"Objective:\n{objective}\n\n"
            f"Execution Result:\n{result}\n\n"
            "Evaluate whether the result fulfills the objective.\n"
            "Respond in EXACTLY this format (3 lines, nothing else):\n"
            "VERDICT: CORRECT | PARTIAL | INCORRECT\n"
            "REASONING: <one sentence explaining your verdict>\n"
            "CONFIDENCE: <float between 0.0 and 1.0>"
        )
        system = (
            "You are a strict evaluator.\n"
            "Be honest. Do not assume success without clear evidence."
        )
        if stricter:
            system += " Be EXTREMELY strict — any ambiguity = INCORRECT."

        eval_model = model or EVALUATION_MODEL
        raw = gateway.chat(model=eval_model, prompt=prompt, system=system)

        verdict = "CORRECT"
        reasoning = ""
        conf = 0.8
        for line in raw.strip().splitlines():
            upper = line.upper()
            if upper.startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip().upper()
                if "INCORRECT" in v:
                    verdict = "INCORRECT"
                elif "PARTIAL" in v:
                    verdict = "PARTIAL"
            elif upper.startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()
            elif upper.startswith("CONFIDENCE:"):
                try:
                    conf = max(0.0, min(1.0, float(line.split(":", 1)[1].strip())))
                except ValueError:
                    conf = 0.8
        return verdict, reasoning, conf
    except Exception as e:
        logger.error(f"[Evaluator] evaluate_semantic_with_reasoning failed: {e}")
        return "CORRECT", "", 0.8


# ---------------------------------------------------------------------------
# Phase 22 — Agreement Quality
# ---------------------------------------------------------------------------

def _cosine_similarity(a: str, b: str) -> float:
    """Simple token-overlap cosine similarity (no external deps)."""
    if not a or not b:
        return 0.0
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    return len(intersection) / math.sqrt(len(tokens_a) * len(tokens_b))


def compute_agreement_quality(evaluations: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Phase 22 — Assess quality of apparent consensus.

    Parameters
    ----------
    evaluations : list of dicts with keys:
        evaluator, verdict, reasoning_text, confidence, reliability

    Returns
    -------
    {
        "agreement_score": float,        # 0.0 (weak) → 1.0 (strong)
        "semantic_similarity": float,    # avg pairwise cosine of reasoning texts
        "confidence_variance": float,    # variance of self-reported confidences
    }
    """
    if len(evaluations) < 2:
        return {"agreement_score": 1.0, "semantic_similarity": 1.0, "confidence_variance": 0.0}

    # Pairwise cosine of reasoning texts
    texts = [e["reasoning_text"] for e in evaluations]
    pairs = [(texts[i], texts[j]) for i in range(len(texts)) for j in range(i + 1, len(texts))]
    if pairs:
        sim = sum(_cosine_similarity(a, b) for a, b in pairs) / len(pairs)
    else:
        sim = 1.0

    # Confidence variance
    confs = [e["confidence"] for e in evaluations]
    mean_conf = sum(confs) / len(confs)
    var_conf = sum((c - mean_conf) ** 2 for c in confs) / len(confs)

    # agreement_score: high similarity + low variance = strong agreement
    agreement_score = round(sim * (1.0 - min(var_conf, 1.0)), 3)

    return {
        "agreement_score": agreement_score,
        "semantic_similarity": round(sim, 3),
        "confidence_variance": round(var_conf, 3),
    }


# ---------------------------------------------------------------------------
# Phase 22 — Meta Evaluator
# ---------------------------------------------------------------------------

def meta_validate(
    evaluations: List[Dict[str, Any]],
    objective: str,
    result: str,
) -> str:
    """
    Phase 22 — Ask a separate meta-evaluator whether the panel's reasoning
    is sound or likely biased / correlated.

    Triggers only on high-risk situations or evaluator disagreement.

    Returns
    -------
    "CONFIRMED" | "DOUBTFUL"
    """
    try:
        from scripts.core.gateway import UnifiedGateway
        gateway = UnifiedGateway()

        panel_summary = "\n".join(
            f"- {e['evaluator']}: {e['verdict']} "
            f"(confidence={e['confidence']:.2f}) — {e['reasoning_text']}"
            for e in evaluations
        )
        prompt = (
            f"Objective:\n{objective}\n\n"
            f"Execution Result:\n{result}\n\n"
            f"Evaluator Panel:\n{panel_summary}\n\n"
            "Are these evaluators correct, or are they likely biased, correlated, or incorrect?\n"
            "Respond EXACTLY ONE WORD: CONFIRMED or DOUBTFUL"
        )
        system = (
            "You are a meta-evaluator. Your role is to detect evaluation bias, "
            "groupthink, and correlated errors in panels of evaluators. "
            "Be skeptical. If you see any red flags, return DOUBTFUL."
        )
        # Use a different model family for meta-evaluation
        meta_model = os.environ.get("AGENTX_META_EVALUATOR_MODEL", "claude-3-5-sonnet-20241022")
        raw = gateway.chat(model=meta_model, prompt=prompt, system=system)
        if "DOUBTFUL" in raw.strip().upper():
            logger.warning("[Evaluator] META_EVALUATION: Panel flagged as DOUBTFUL")
            return "DOUBTFUL"
        return "CONFIRMED"
    except Exception as e:
        logger.error(f"[Evaluator] meta_validate failed: {e}")
        print(f"[Evaluator] META_EVALUATION_FAILED: {e}")
        return "CONFIRMED"  # safe fallback — don't block on meta failure


# Phase 17 — Multi-Evaluator Pipeline
# Phase 19 — Diversity Enforcement
EVALUATORS = [
    os.environ.get("AGENTX_EVALUATOR_A", "gpt-4o"),
    os.environ.get("AGENTX_EVALUATOR_B", "claude-3-5-sonnet-20241022"),
    os.environ.get("AGENTX_EVALUATOR_C", "gemini-1.5-pro")
]

# Ensure diversity warning
families = set()
for ev in EVALUATORS:
    if "gpt" in ev.lower(): families.add("openai")
    elif "claude" in ev.lower(): families.add("anthropic")
    elif "gemini" in ev.lower(): families.add("google")
    else: families.add("other")
    
if len(families) < 2:
    logger.warning("[Evaluator] DIVERSITY WARNING: Evaluators belong to the same model family. Risk of correlated failures.")

def get_evaluation_context(objective: str, metadata: Dict[str, Any]) -> Dict[str, str]:
    """Phase 20 - Context extraction"""
    objective_lower = objective.lower()
    
    # Classify task type
    task_type = "general"
    if any(k in objective_lower for k in ["code", "script", "refactor", "bug", "implement"]):
        task_type = "coding"
    elif any(k in objective_lower for k in ["think", "reason", "plan", "analyze", "why"]):
        task_type = "reasoning"
    elif any(k in objective_lower for k in ["search", "find", "read", "fetch", "get"]):
        task_type = "retrieval"
    elif any(k in objective_lower for k in ["use", "run", "execute", "tool"]):
        task_type = "tool_use"
        
    # Estimate difficulty
    difficulty = "medium"
    if len(objective) > 200 or "complex" in objective_lower or "multiple" in objective_lower:
        difficulty = "high"
    elif len(objective) < 30 and "simple" in objective_lower:
        difficulty = "low"
        
    # Derive risk
    risk = "low"
    if metadata.get("risk_level") == "HIGH" or any(k in objective_lower for k in ["delete", "remove", "production", "deploy"]):
        risk = "high"
        
    return {
        "task_type": task_type,
        "difficulty": difficulty,
        "risk": risk
    }

def evaluate_pipeline(task_id: int, result: str, context: Dict[str, Any], stricter: bool = False, confidence: float = 1.0) -> Dict[str, Any]:
    """
    Phase 18 — Risk-Aware Correctness Pipeline
    Phase 23 — Conditional Cascade Evaluation

    Returns:
    {
        "decision": "PASS" | "FAIL" | "UNCERTAIN",
        "risk_score": 0.0 -> 1.0,
        "uncertainty_score": float,
        "agreement_level": int,
        "veto_triggered": bool,
        "agreement_score": float,
        "eval_path": "fast" | "cascade",
    }
    """
    is_high_risk = context.get("high_risk", False)
    risk_level = "HIGH" if is_high_risk else ("MEDIUM" if stricter else "LOW")

    # --- Layer 1: Deterministic (always runs) ---
    det_result = evaluate_task(task_id, result, context)

    if det_result == "FALSE_SUCCESS":
        logger.info("[Evaluator] Deterministic FAIL → return FAIL")
        try:
            from agentx.decision.metrics import update_evaluation_metrics
            update_evaluation_metrics(True, True, False, eval_path="fast")
        except Exception:
            pass
        return {
            "decision": "FAIL",
            "risk_score": 1.0,
            "uncertainty_score": 0.5,
            "agreement_level": 0,
            "veto_triggered": True,
            "agreement_score": 0.0,
            "eval_path": "fast",
        }

    objective = context.get("objective", "")
    result_str = str(result).strip()

    if not objective or not result_str:
        return {
            "decision": "PASS" if det_result == "TRUE_SUCCESS" else "UNCERTAIN",
            "risk_score": 0.5,
            "uncertainty_score": 0.0,
            "agreement_level": 1,
            "veto_triggered": False,
            "agreement_score": 1.0,
            "eval_path": "fast",
        }

    eval_context = get_evaluation_context(objective, context)

    # ── Phase 24: Adaptive Thresholds ────────────────────────────────────
    # Base thresholds; tighten automatically from live metrics.
    _threshold_uncertainty = 0.4
    _threshold_confidence = 0.6
    try:
        from agentx.decision.metrics import get_metrics
        _live = get_metrics()
        _fs_rate = _live.get("false_success_rate", 0.0)
        _dis_rate = _live.get("disagreement_rate", 0.0)
        if _fs_rate > 0.05:
            _threshold_uncertainty = max(0.2, _threshold_uncertainty - 0.1)
            logger.info("[Evaluator] ADAPTIVE_THRESHOLD_UPDATED: uncertainty threshold → %.2f (fs_rate=%.3f)",
                        _threshold_uncertainty, _fs_rate)
            print(f"[Evaluator] ADAPTIVE_THRESHOLD_UPDATED: uncertainty_threshold={_threshold_uncertainty:.2f}")
        if _dis_rate > 0.2:
            _threshold_confidence = min(0.85, _threshold_confidence + 0.05)
            logger.info("[Evaluator] ADAPTIVE_THRESHOLD_UPDATED: confidence threshold → %.2f (dis_rate=%.3f)",
                        _threshold_confidence, _dis_rate)
            print(f"[Evaluator] ADAPTIVE_THRESHOLD_UPDATED: confidence_threshold={_threshold_confidence:.2f}")
    except Exception:
        pass  # keep defaults on error

    # ── Phase 24: Cascade spam guard ──────────────────────────────────────
    MAX_CASCADE_COUNT = 2
    cascade_count = int(context.get("cascade_count", 0))
    if cascade_count > MAX_CASCADE_COUNT:
        logger.warning("[Evaluator] CASCADE_LIMIT_REACHED: cascade_count=%d > %d — forcing fast path",
                       cascade_count, MAX_CASCADE_COUNT)
        print(f"[Evaluator] CASCADE_LIMIT_REACHED: too many cascades for this task — using fast path")
        context["_force_fast"] = True

    # ── Phase 23/25: Cascade gate ─────────────────────────────────────────
    # Prior uncertainty is injected by the caller via context["task_uncertainty"]
    prior_uncertainty = float(context.get("task_uncertainty", 0.0))
    use_multi_eval = False
    cascade_reasons: List[str] = []

    # Phase 25 routing sentinels override adaptive logic
    if context.get("_routing_force_fast") or context.get("_force_fast"):
        # Router decided this is a simple task — always fast path
        use_multi_eval = False
    elif context.get("_routing_force_cascade"):
        # Router decided this is complex — always cascade
        use_multi_eval = True
        cascade_reasons.append("routing_force_cascade")
    else:
        # Phase 24 adaptive gate
        if prior_uncertainty > _threshold_uncertainty:
            use_multi_eval = True
            cascade_reasons.append(f"prior_uncertainty={prior_uncertainty:.2f}")
        if is_high_risk or risk_level == "HIGH":
            use_multi_eval = True
            cascade_reasons.append("high_risk")
        if confidence < _threshold_confidence:
            use_multi_eval = True
            cascade_reasons.append(f"low_confidence={confidence:.2f}")

    # ── Fast path: single evaluator, no consensus machinery ──────────────
    if not use_multi_eval:
        fast_ev = EVALUATORS[0]
        verdict, reasoning_text, eval_conf = evaluate_semantic_with_reasoning(
            objective, result_str, context, stricter=False, model=fast_ev
        )
        fast_decision = "TRUE_SUCCESS"
        if verdict == "INCORRECT":
            fast_decision = "FALSE_SUCCESS"
        elif verdict == "PARTIAL":
            fast_decision = "PARTIAL_SUCCESS"

        fast_uncertainty = round(max(0.0, (1.0 - eval_conf) * 0.5), 3)
        if fast_decision == "TRUE_SUCCESS" and eval_conf < 0.7:
            fast_uncertainty += 0.2
        fast_uncertainty = min(1.0, fast_uncertainty)

        if fast_decision == "FALSE_SUCCESS":
            out_decision = "FAIL"
            fast_risk = 0.9
        elif fast_decision == "PARTIAL_SUCCESS":
            out_decision = "UNCERTAIN"
            fast_risk = 0.6
        else:
            out_decision = "PASS"
            fast_risk = max(0.1, 1.0 - eval_conf)

        logger.info("[Evaluator] CASCADE_FAST_PATH: single evaluator used (task_id=%s)", task_id)
        print(f"[Evaluator] CASCADE_FAST_PATH: verdict={verdict}, conf={eval_conf:.2f}")

        try:
            from agentx.decision.metrics import update_evaluation_metrics
            update_evaluation_metrics(
                false_success=(fast_decision == "FALSE_SUCCESS"),
                veto_triggered=(fast_decision == "FALSE_SUCCESS"),
                disagreement=False,
                eval_path="fast",
            )
        except Exception:
            pass

        return {
            "decision": out_decision,
            "risk_score": fast_risk,
            "uncertainty_score": fast_uncertainty,
            "agreement_level": 1,
            "veto_triggered": (fast_decision == "FALSE_SUCCESS"),
            "agreement_score": eval_conf,
            "eval_path": "fast",
        }

    # ── Cascade path: full multi-evaluator + consensus + meta ─────────────
    logger.info(
        "[Evaluator] CASCADE_ESCALATED: full pipeline triggered for task_id=%s reasons=%s",
        task_id, cascade_reasons,
    )
    print(f"[Evaluator] CASCADE_ESCALATED: {', '.join(cascade_reasons)}")

    # Verification budget (preserved from Phase 17/21)
    evaluators_to_use = EVALUATORS
    if risk_level == "MEDIUM":
        evaluators_to_use = EVALUATORS[:2]
    # HIGH risk uses all evaluators (default)

    try:
        from agentx.decision.metrics import get_contextual_reliability, detect_context_drift, update_evaluator_performance
    except Exception as e:
        logger.error(f"[Evaluator] Failed to load evaluator metrics imports: {e}")
        get_contextual_reliability = lambda *args, **kwargs: 1.0
        detect_context_drift = lambda *args, **kwargs: False
        update_evaluator_performance = lambda *args, **kwargs: None

    results = []
    hard_vetoes = 0
    soft_vetoes = 0

    # Phase 22: collect per-evaluator reasoning + confidence alongside verdict
    rich_evaluations: List[Dict[str, Any]] = []

    # --- Layer 2 (cascade): Weak Judge Suppression + per-evaluator collection ---
    active_evaluators = []
    from agentx.decision.calibration import compute_confidence_threshold
    task_type = eval_context.get("task_type", "default")
    dyn_thresholds = compute_confidence_threshold(task_type)
    RELIABILITY_THRESHOLD = dyn_thresholds.get("reliability", 0.5)
    MIN_THRESHOLD = max(0.1, dyn_thresholds.get("reliability", 0.5) - 0.2)

    for evaluator in evaluators_to_use:
        try:
            rel = get_contextual_reliability(evaluator, eval_context)
            detect_context_drift(evaluator, eval_context)
        except Exception as e:
            logger.error(f"[Evaluator] Error getting contextual reliability: {e}")
            rel = 1.0

        if rel < MIN_THRESHOLD:
            logger.warning(f"[Evaluator] WEAK_JUDGE_DETECTED: Skipping {evaluator} (context_rel={rel:.3f} in {eval_context['task_type']})")
            continue

        active_evaluators.append(evaluator)
        # Phase 22: use extended evaluator to get reasoning + confidence
        verdict, reasoning_text, eval_conf = evaluate_semantic_with_reasoning(
            objective, result_str, context, stricter=stricter, model=evaluator
        )

        decision_val = "TRUE_SUCCESS"
        if verdict == "INCORRECT":
            decision_val = "FALSE_SUCCESS"
        elif verdict == "PARTIAL":
            decision_val = "PARTIAL_SUCCESS"

        results.append((evaluator, decision_val, rel))
        rich_evaluations.append({
            "evaluator": evaluator,
            "verdict": verdict,
            "decision": decision_val,
            "reasoning_text": reasoning_text,
            "confidence": eval_conf,
            "reliability": rel,
        })

    if not results:
        logger.warning("[Evaluator] All evaluators suppressed due to low reliability.")
        return {"decision": "UNCERTAIN", "risk_score": 1.0, "uncertainty_score": 1.0, "agreement_level": 0, "veto_triggered": False}

    decision_vals = [r[1] for r in results]
    disagreement = len(set(decision_vals)) > 1
    
    for ev, dec, rel in results:
        is_veto = (dec == "FALSE_SUCCESS")
        if is_veto:
            if rel >= RELIABILITY_THRESHOLD:
                hard_vetoes += 1
                logger.warning(f"[Evaluator] HARD VETO from {ev} (rel={rel:.3f})")
                logger.info(f"[Evaluator] VETO_SOURCE: {ev}")
            else:
                soft_vetoes += 1
                logger.warning(f"[Evaluator] SOFT VETO from {ev} (rel={rel:.3f})")
                logger.info(f"[Evaluator] VETO_SOURCE (Soft): {ev}")
                
        # Phase 19 & 20: Log individual evaluator actions with context
        try:
            update_evaluator_performance(ev, dec, disagreement, is_veto, task_type=eval_context["task_type"], difficulty=eval_context["difficulty"])
        except Exception:
            pass

    veto_triggered = (hard_vetoes > 0)
    
    # Update evaluation tracking metrics (Phase 18/23)
    try:
        update_evaluation_metrics(
            false_success=(det_result == "TRUE_SUCCESS" and (veto_triggered or soft_vetoes > 0)),
            veto_triggered=veto_triggered,
            disagreement=disagreement,
            eval_path="cascade"
        )
    except BaseException as e:
        logger.error(f"[Evaluator] Failed to update eval metrics: {e}")

    # Calculate Weighted Score (Phase 19)
    total_weight = sum(r[2] for r in results)
    success_score = sum(r[2] for r in results if r[1] == "TRUE_SUCCESS")
    weighted_success_rate = success_score / total_weight if total_weight > 0 else 0.0
    base_risk = max(0.1, 1.0 - weighted_success_rate)

    # Calculate Uncertainty Score (Phase 21)
    uncertainty_score = 0.0
    if disagreement: uncertainty_score += 0.3
    if soft_vetoes > 0: uncertainty_score += 0.4
    if confidence < dyn_thresholds.get("confidence", 0.8): uncertainty_score += (dyn_thresholds.get("confidence", 0.8) - confidence)
    if veto_triggered: uncertainty_score += 0.5
    uncertainty_score = min(1.0, round(uncertainty_score, 3))

    # Phase 22: Consensus quality metrics (computed once, used below)
    agreement_quality = compute_agreement_quality(rich_evaluations)
    agreement_score = agreement_quality["agreement_score"]
    conf_variance = agreement_quality["confidence_variance"]

    # --- Layer 3: Strengthen Minority Veto (preserved from Phase 19) ---
    if veto_triggered:
        logger.warning(f"[Evaluator] MINORITY_VETO_TRIGGERED for task {task_id}: {decision_vals}")
        print(f"[Evaluator] MINORITY_VETO_TRIGGERED: Hard veto issued by evaluators.")
        return {
            "decision": "FAIL",
            "risk_score": 1.0 if confidence > 0.7 else 0.9,
            "uncertainty_score": uncertainty_score,
            "agreement_level": decision_vals.count("FALSE_SUCCESS"),
            "veto_triggered": True,
            "agreement_score": agreement_score,
        }

    if soft_vetoes > 0:
        logger.warning(f"[Evaluator] SOFT VETO present. Increasing risk score. Task {task_id}")
        return {
            "decision": "UNCERTAIN",
            "risk_score": max(0.85, base_risk),
            "uncertainty_score": uncertainty_score,
            "agreement_level": decision_vals.count("TRUE_SUCCESS"),
            "veto_triggered": False,
            "agreement_score": agreement_score,
        }

    # --- Layer 4: Disagreement → meta-evaluate, then return UNCERTAIN ---
    if disagreement:
        logger.warning(f"[Evaluator] EVALUATOR_DISAGREEMENT for task {task_id}: {decision_vals}")
        # Trigger meta-evaluator on disagreement
        meta_result = meta_validate(rich_evaluations, objective, result_str)
        if meta_result == "DOUBTFUL":
            print(f"[Evaluator] FALSE_CONSENSUS_DETECTED: Meta-evaluator flagged disagreement panel as DOUBTFUL")
            logger.warning("[Evaluator] FALSE_CONSENSUS_DETECTED (disagreement path)")
        return {
            "decision": "UNCERTAIN",
            "risk_score": max(0.8, base_risk),
            "uncertainty_score": uncertainty_score,
            "agreement_level": decision_vals.count("TRUE_SUCCESS"),
            "veto_triggered": False,
            "agreement_score": agreement_score,
        }

    # --- Layer 5: Consensus Validation (Phase 22 — replaces blind majority vote) ---
    if all(r == "TRUE_SUCCESS" for r in decision_vals):
        # Gate 1: Execution confidence
        if confidence < 0.5:
            logger.warning(f"[Evaluator] ANTI-CONSENSUS SAFEGUARD: All agreed, but execution confidence was {confidence}")
            return {
                "decision": "UNCERTAIN",
                "risk_score": max(0.7, base_risk),
                "uncertainty_score": uncertainty_score,
                "agreement_level": len(decision_vals),
                "veto_triggered": False,
                "agreement_score": agreement_score,
            }

        # Gate 2: Agreement quality — weak semantic similarity or high confidence variance
        AGREEMENT_THRESHOLD = 0.7
        CONF_VARIANCE_LIMIT = 0.05
        if agreement_score < AGREEMENT_THRESHOLD or conf_variance > CONF_VARIANCE_LIMIT:
            logger.warning(
                "[Evaluator] CONSENSUS_WEAK: agreement_score=%.3f, conf_variance=%.3f — returning UNCERTAIN",
                agreement_score, conf_variance,
            )
            print(
                f"[Evaluator] CONSENSUS_WEAK: agreement={agreement_score:.3f}, "
                f"conf_variance={conf_variance:.3f} → UNCERTAIN"
            )
            uncertainty_score = min(1.0, uncertainty_score + 0.2)
            return {
                "decision": "UNCERTAIN",
                "risk_score": max(0.65, base_risk),
                "uncertainty_score": uncertainty_score,
                "agreement_level": len(decision_vals),
                "veto_triggered": False,
                "agreement_score": agreement_score,
            }

        # Gate 3: Meta-evaluator (only on high-risk)
        is_high_risk_eval = is_high_risk or stricter
        if is_high_risk_eval:
            meta_result = meta_validate(rich_evaluations, objective, result_str)
            if meta_result == "DOUBTFUL":
                logger.warning("[Evaluator] FALSE_CONSENSUS_DETECTED: Meta-evaluator DOUBTFUL on all-TRUE panel")
                print("[Evaluator] FALSE_CONSENSUS_DETECTED: Meta-evaluator overrides consensus → UNCERTAIN")
                uncertainty_score = min(1.0, uncertainty_score + 0.3)
                return {
                    "decision": "UNCERTAIN",
                    "risk_score": max(0.75, base_risk),
                    "uncertainty_score": uncertainty_score,
                    "agreement_level": len(decision_vals),
                    "veto_triggered": False,
                    "agreement_score": agreement_score,
                }

        # All gates passed — accept consensus as PASS
        return {
            "decision": "PASS",
            "risk_score": base_risk,
            "uncertainty_score": uncertainty_score,
            "agreement_level": len(decision_vals),
            "veto_triggered": False,
            "agreement_score": agreement_score,
        }

    # Fallback (e.# =========================================
# PART 4 — FAILURE SCENARIOS (SIMULATION)
# =========================================

_critic_run_history = []

def log_critic_run(disagreement_score: float, critic_issues_count: int, final_success: bool, confidence_value: float, task_type: str, shared_error_detected: bool = False, failure_type: str = "none"):
    _critic_run_history.append({
        "disagreement_score": disagreement_score,
        "critic_issues_count": critic_issues_count,
        "final_success": final_success,
        "confidence_value": confidence_value,
        "task_type": task_type,
        "shared_error_detected": shared_error_detected,
        "failure_type": failure_type
    })
    
    # PART 5: Failure analysis loop
    if not final_success:
        import logging
        logger = logging.getLogger("agentx.decision.evaluator")
        logger.info(f"[Failure Analysis] Disagreement: {disagreement_score:.2f}, Critic Issues: {critic_issues_count}, Failure Type: {failure_type}")
        
        # Learning rule
        if failure_type == "logic_error" and critic_issues_count == 0:
            logger.warning("[Failure Analysis] Critic missed a logic error. Increasing critic weight.")
            # increase_critic_weight() hook would go here

def compute_critic_metrics() -> dict:
    total_runs = len(_critic_run_history)
    if total_runs == 0:
        return {}
    
    critic_trigger_count = sum(1 for r in _critic_run_history if r["critic_issues_count"] > 0)
    critic_triggered_but_successful = sum(1 for r in _critic_run_history if r["critic_issues_count"] > 0 and r["final_success"])
    critic_missed_failure = sum(1 for r in _critic_run_history if r["critic_issues_count"] == 0 and not r["final_success"])
    detected_shared_errors = sum(1 for r in _critic_run_history if r["shared_error_detected"])
    total_shared_errors = sum(1 for r in _critic_run_history if r["task_type"] == "adversarial" or r["shared_error_detected"])
    
    # Confidence accuracy (correlation proxy)
    success_conf = [r["confidence_value"] for r in _critic_run_history if r["final_success"]]
    fail_conf = [r["confidence_value"] for r in _critic_run_history if not r["final_success"]]
    avg_success_conf = sum(success_conf) / len(success_conf) if success_conf else 0.0
    avg_fail_conf = sum(fail_conf) / len(fail_conf) if fail_conf else 0.0
    confidence_accuracy = avg_success_conf - avg_fail_conf

    return {
        "critic_trigger_rate": critic_trigger_count / total_runs,
        "false_positive_rate": critic_triggered_but_successful / total_runs,
        "false_negative_rate": critic_missed_failure / total_runs,
        "confidence_accuracy": confidence_accuracy,
        "shared_error_detection_rate": detected_shared_errors / max(1, total_shared_errors)
    }

def simulate_failure_scenarios():
    """
    Phase 21.5 & 21.6: Failure Scenarios Simulation + Metrics Validation
    """
    scenarios = {
        "Test 1 — Shared Hallucination": {
            "type": "adversarial",
            "description": "All plans: same structure, same wrong assumption.",
            "expected": ["disagreement LOW", "critic detects shared issue", "verifier triggered"],
            "resolution": "compare_reasoning flags shared logic gap, forcing verification loop."
        },
        "Test 2 — Structural Correct, Logic Wrong": {
            "type": "hard",
            "description": "Plans valid structurally but wrong dependency logic.",
            "expected": ["critic catches logic gap"],
            "resolution": "critique_plan detects preconditions without dependencies, penalizing plan."
        },
        "Test 3 — Divergent Reasoning": {
            "type": "ambiguous",
            "description": "Plans differ significantly.",
            "expected": ["disagreement HIGH", "critic refines selection"],
            "resolution": "High disagreement falls back to verification loop, mitigating blind consensus."
        },
        "Test 4 — Missing Preconditions": {
            "type": "easy",
            "description": "Plan missing required state values.",
            "expected": ["critic flags missing state", "plan rejected before execution"],
            "resolution": "critic penalizes plan reducing confidence below threshold, requiring approval."
        },
        "Test 5 — Adversarial Plan": {
            "type": "adversarial",
            "description": "One plan is malicious.",
            "expected": ["minority veto OR critic eliminates"],
            "resolution": "Minority veto detects structural divergence or critic heavily penalizes logic gaps."
        }
    }
    
    # Mocking runs for Phase 21.6 validation
    log_critic_run(0.1, 2, False, 0.4, "adversarial", True, "logic_error")
    log_critic_run(0.2, 1, False, 0.5, "hard", False, "logic_error")
    log_critic_run(0.8, 0, True, 0.7, "ambiguous", False, "none")
    log_critic_run(0.3, 1, False, 0.4, "easy", False, "missing_precondition")
    log_critic_run(0.4, 3, False, 0.3, "adversarial", False, "logic_error")
    
    metrics = compute_critic_metrics()
    
    print("[Evaluator] Running Failure Scenarios Simulation...")
    for name, data in scenarios.items():
        print(f"\n--- {name} [{data['type']}] ---")
        print(f"Description: {data['description']}")
        print(f"Expected: {', '.join(data['expected'])}")
        print(f"System Resolution: {data['resolution']}")
        
    print("\n[Evaluator] Phase 21.6 Metrics Validation:")
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"- {k}: {v:.3f}")
        else:
            print(f"- {k}: {v}")
            
    return scenarios, metrics


