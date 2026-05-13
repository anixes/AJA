import json
import logging
import os
from typing import Dict, Any, List
from agentx.orchestration.gateway import UnifiedGateway

logger = logging.getLogger("agent.decision")

# Phase 15 — if confidence drops below this after all biasing, force ASK
UNCERTAINTY_THRESHOLD = 0.45

# Minimum context keys expected at decision time
_REQUIRED_CONTEXT_KEYS = ["objective"]


class DecisionEngine:
    """
    LLM-assisted decision engine for Agent.
    Determines the best execution path for a given objective.
    """
    
    SYSTEM_PROMPT = """
    You are a decision engine. You DO NOT execute tasks.
    You ONLY choose the best execution path.

    Available actions:
    * SKILL: use existing skill (best for exact matches or high-confidence variations)
    * COMPOSE: chain multiple skills (best for multi-step complex objectives)
    * NEW: run normal execution (fallback to SwarmEngine for unknown/novel tasks)
    * ASK: request clarification from user (for ambiguous or missing info)
    * REJECT: objective is unsafe, malicious, or clearly invalid

    Return JSON only in the following format:
    {
      "type": "SKILL | COMPOSE | NEW | ASK | REJECT",
      "confidence": float (0.0 to 1.0),
      "reason": "short explanation of why this path was chosen",
      "evidence": ["list of factual reasons for this decision"]
    }
    """

    def __init__(self):
        try:
            self.gateway = UnifiedGateway()
        except Exception:
            self.gateway = None

    def decide(self, objective: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decide the execution path based on objective and context.
        """
        if not self.gateway or not self.gateway.api_key:
            logger.warning("No LLM gateway available. Falling back to NEW.")
            return {"type": "NEW", "confidence": 1.0, "reason": "No LLM gateway configured."}

        # --- Phase 15: Context freshness validation ---
        _stale = [k for k in _REQUIRED_CONTEXT_KEYS if k not in context]
        if _stale:
            context.setdefault("objective", objective)
            logger.info("[Engine] CONTEXT_REFRESHED: filled missing keys %s", _stale)
            print(f"[Engine] CONTEXT_REFRESHED: added missing context keys {_stale}")

        # --- Decision Feedback (Phase 10 Self-Improvement) ---
        feedback_stats = {}
        similar_decisions = []
        try:
            from agentx.decision.feedback import get_feedback_stats, get_similar_decisions
            feedback_stats = get_feedback_stats(objective)
            context["feedback_stats"] = feedback_stats
            
            # Phase 10 - Long term memory
            similar_decisions = get_similar_decisions(objective)
            if similar_decisions:
                print(f"[Decision] DECISION_MEMORY_USED: Found {len(similar_decisions)} similar past decisions.")
                context["similar_decisions"] = similar_decisions
        except ImportError:
            pass

        # --- Decision Metrics Injection (Phase 13) ---
        try:
            from agentx.decision.metrics import get_metrics_summary_for_prompt, get_metrics
            _metrics_summary = get_metrics_summary_for_prompt()
            _metrics_data = get_metrics()
            if _metrics_summary:
                context["metrics_summary"] = _metrics_summary
                context["metrics_data"] = _metrics_data
        except Exception:
            _metrics_data = {}

        
        try:
            # Phase 16: Local gemma model for offline inference
            _model = "llama_cpp:gemma-4-e2b"
            prompt = self._build_prompt(objective, context)
            raw_response = self.gateway.chat(
                model=_model,
                prompt=prompt,
                system=self.SYSTEM_PROMPT
            )
            
            decision = self._parse_and_validate(raw_response, context)
            if "evidence" not in decision:
                decision["evidence"] = []

            # Add context signals to evidence
            if context.get("top_skills"):
                decision["evidence"].append(f"Matched {len(context['top_skills'])} skills")

            # --- Apply Biasing Logic ---
            if feedback_stats and decision["type"] in feedback_stats:
                stats = feedback_stats[decision["type"]]
                if stats["FAILURE"] >= 2:
                    old_conf = decision["confidence"]
                    decision["confidence"] = max(0.0, decision["confidence"] - 0.3)
                    decision["reason"] += f" (Penalized from {old_conf} due to repeated failure)"
                    decision["evidence"].append(f"Penalized {decision['type']} due to {stats['FAILURE']} failures on exact match")
                
                if stats["SUCCESS"] > 0:
                    decision["confidence"] = min(1.0, decision["confidence"] + 0.1)
                    decision["evidence"].append(f"Boosted {decision['type']} due to prior success on exact match")

            # --- Apply Long-term Memory Biasing ---
            if similar_decisions:
                sim_fails = sum(1 for s in similar_decisions if s["decision_type"] == decision["type"] and s["outcome"] == "FAILURE")
                sim_success = sum(1 for s in similar_decisions if s["decision_type"] == decision["type"] and s["outcome"] == "SUCCESS")
                
                if sim_fails >= 2:
                    decision["confidence"] = max(0.0, decision["confidence"] - 0.2)
                    decision["evidence"].append(f"Repeated similar failures for {decision['type']} ({sim_fails} times)")
                elif sim_success > 0:
                    decision["confidence"] = min(1.0, decision["confidence"] + 0.1)
                    decision["evidence"].append(f"Repeated similar success for {decision['type']} ({sim_success} times)")

            # --- Apply System State Biasing ---
            state = context.get("system_state", {})
            if state:
                if state.get('load_level') == 'HIGH' and decision['type'] == 'COMPOSE':
                    decision['confidence'] = max(0.0, decision['confidence'] - 0.3)
                    decision['evidence'].append("Discouraged COMPOSE due to HIGH system load")
                
                if state.get('failed_tasks', 0) > 3 and decision['type'] == 'SKILL':
                    decision['confidence'] = max(0.0, decision['confidence'] - 0.2)
                    decision['evidence'].append("Discouraged SKILL due to high recent failure rate")
                
                if not state.get('is_healthy', True):
                    if decision['type'] not in ['ASK', 'NEW', 'REJECT']:
                        decision['confidence'] = max(0.0, decision['confidence'] - 0.4)
                        decision['evidence'].append("Penalized complex task due to UNHEALTHY system state")

            # --- Apply Metrics Biasing (Phase 13) ---
            # Soft penalty only — metrics influence, never enforce
            try:
                _mdata = context.get("metrics_data", {})
                _per_type = _mdata.get("per_type", {})
                if decision["type"] in _per_type and _per_type[decision["type"]]["total"] >= 5:
                    _acc = _per_type[decision["type"]]["accuracy"]
                    if _acc < 0.4:
                        _penalty = round((0.4 - _acc) * 0.5, 2)  # max ~0.2 penalty
                        decision["confidence"] = max(0.0, decision["confidence"] - _penalty)
                        decision["evidence"].append(
                            f"Soft penalty: {decision['type']} has {int(_acc*100)}% historical accuracy"
                        )
                    elif _acc > 0.75:
                        decision["confidence"] = min(1.0, decision["confidence"] + 0.05)
                        decision["evidence"].append(
                            f"Soft boost: {decision['type']} has {int(_acc*100)}% historical accuracy"
                        )
            except Exception:
                pass

            # --- Phase 24: Uncertainty Trend & False-Success Biasing ---
            # Penalise complex paths when system is in a degraded state.
            try:
                _mdata = context.get("metrics_data", {})
                _trend = _mdata.get("uncertainty_trend", "stable")
                _fs_rate = _mdata.get("false_success_rate", 0.0)

                if _trend == "rising" and decision["type"] == "COMPOSE":
                    _penalty = 0.15
                    decision["confidence"] = max(0.0, decision["confidence"] - _penalty)
                    decision["evidence"].append(
                        "UNCERTAINTY_TREND_RISING: COMPOSE penalised (uncertainty is rising)"
                    )
                    logger.info("[Engine] UNCERTAINTY_TREND_RISING: penalised COMPOSE by %.2f", _penalty)
                    print(f"[Engine] UNCERTAINTY_TREND_RISING: COMPOSE confidence reduced by {_penalty}")

                if _fs_rate > 0.08 and decision["type"] == "SKILL":
                    _penalty = round(min(0.2, _fs_rate * 1.5), 2)
                    decision["confidence"] = max(0.0, decision["confidence"] - _penalty)
                    decision["evidence"].append(
                        f"HIGH_FALSE_SUCCESS_RATE: SKILL reuse penalised (fs_rate={_fs_rate:.2%})"
                    )
                    logger.info("[Engine] HIGH_FALSE_SUCCESS_RATE: penalised SKILL by %.2f", _penalty)
            except Exception:
                pass

            # --- Phase 15: Uncertainty Hard Gate ---
            if decision.get("confidence", 1.0) < UNCERTAINTY_THRESHOLD:
                logger.warning(
                    "[Engine] UNCERTAINTY_TRIGGERED: confidence=%.2f < threshold=%.2f for type=%s",
                    decision["confidence"], UNCERTAINTY_THRESHOLD, decision.get("type")
                )
                print(f"[Engine] UNCERTAINTY_TRIGGERED: confidence {decision['confidence']:.2f} < {UNCERTAINTY_THRESHOLD} → forcing ASK")
                decision["type"] = "ASK"
                decision["reason"] += f" (Forced ASK: confidence {decision['confidence']:.2f} below uncertainty threshold)"
                decision["evidence"].append(
                    f"UNCERTAINTY_TRIGGERED: confidence {decision['confidence']:.2f} < {UNCERTAINTY_THRESHOLD}"
                )

            return decision
        except Exception as e:
            logger.error(f"Decision engine failure: {str(e)}")
            return {"type": "NEW", "confidence": 0.0, "reason": f"Error: {str(e)}"}

    def _build_prompt(self, objective: str, context: Dict[str, Any]) -> str:
        skills = context.get("top_skills", [])
        history = context.get("task_history", [])
        risk = context.get("risk_level", "LOW")
        feedback = context.get("feedback_stats", {})
        
        prompt = f"Objective: {objective}\n"
        prompt += f"Risk Level: {risk}\n\n"
        
        if feedback:
            prompt += "Previous Decision Outcomes for this exact objective:\n"
            for dtype, stats in feedback.items():
                prompt += f"- {dtype}: {stats['SUCCESS']} successes, {stats['FAILURE']} failures, {stats['FALLBACK']} fallbacks\n"
            prompt += "\n"

        similar = context.get("similar_decisions", [])
        if similar:
            prompt += "Similar Past Objectives and Outcomes:\n"
            for s in similar[:5]:  # limit to top 5
                prompt += f"- '{s.get('original_objective', 'unknown')}': chose {s.get('decision_type')} -> {s.get('outcome')}\n"
            prompt += "\n"

        if skills:
            prompt += "Matching Skills:\n"
            for s in skills:
                # Provide only relevant fields to save tokens
                prompt += f"- {s.get('name')} (id: {s.get('id')[:8]}, Conf: {s.get('confidence_score')}, Risk: {s.get('risk_level')})\n"
        
        if history:
            prompt += "\nRecent Global Task History:\n"
            for t in history:
                prompt += f"- {t.get('input')} -> {t.get('status')}\n"
                
        state = context.get("system_state", {})
        if state:
            prompt += f"\nSystem State:\n"
            prompt += f"* Load: {state.get('load_level', 'UNKNOWN')}\n"
            prompt += f"* Recent Failures: {state.get('failed_tasks', 0)}\n"
            prompt += f"* Stalled Tasks: {state.get('pending_tasks', 0)} pending (potential stall)\n"
            prompt += f"* Health: {'HEALTHY' if state.get('is_healthy', True) else 'UNHEALTHY'}\n"

        # --- Phase 13: System Metrics ---
        metrics_summary = context.get("metrics_summary", "")
        if metrics_summary:
            prompt += f"\n{metrics_summary}\n"

        return prompt

    def _parse_and_validate(self, response: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            # Extract JSON if wrapped in code blocks
            clean_response = response.strip()
            if "```json" in clean_response:
                clean_response = clean_response.split("```json")[1].split("```")[0]
            elif "```" in clean_response:
                clean_response = clean_response.split("```")[1].split("```")[0]
            
            data = json.loads(clean_response)
            
            # 1. Validate Schema
            required = ["type", "confidence", "reason"]
            if not all(k in data for k in required):
                raise ValueError("Missing required fields in JSON")
            
            return data
        except Exception as e:
            logger.error(f"Failed to parse LLM decision: {str(e)}")
            return {"type": "NEW", "confidence": 0.0, "reason": f"Parse error: {str(e)}"}

def decide(objective: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Standalone wrapper for the decision engine."""
    return DecisionEngine().decide(objective, context)


# ---------------------------------------------------------------------------
# Phase 25 — Predictive Routing: Task Difficulty Estimator
# ---------------------------------------------------------------------------

# Action verbs that signal multi-step complexity
_MULTI_STEP_VERBS = {
    "fetch", "analyze", "send", "parse", "transform", "upload", "download",
    "generate", "compare", "filter", "extract", "summarise", "summarize",
    "validate", "deploy", "monitor", "convert", "merge", "split",
}


def estimate_task_difficulty(objective: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Phase 25 — Heuristic difficulty estimator for predictive routing.

    Returns a dict with:
        complexity           : float 0–1
        expected_uncertainty : float 0–1
        expected_cost        : float 0–1   (relative, not dollars)

    All fields are derived from pure heuristics — no LLM call.
    """
    objective_lower = objective.lower()
    words = objective_lower.split()
    complexity = 0.0

    # ── Heuristic 1: Objective length ──────────────────────────────────────
    word_count = len(words)
    if word_count > 60:
        complexity += 0.30
    elif word_count > 30:
        complexity += 0.15
    elif word_count > 15:
        complexity += 0.05

    # ── Heuristic 2: Multi-verb / multi-step indicators ────────────────────
    matched_verbs = [v for v in _MULTI_STEP_VERBS if v in words]
    if len(matched_verbs) >= 3:
        complexity += 0.30
    elif len(matched_verbs) == 2:
        complexity += 0.15
    elif len(matched_verbs) == 1:
        complexity += 0.05

    # ── Heuristic 3: Past failure rate ────────────────────────────────────
    try:
        from agentx.decision.metrics import get_metrics
        _m = get_metrics()
        _fs = _m.get("false_success_rate", 0.0)
        if _fs > 0.15:
            complexity += 0.20
        elif _fs > 0.05:
            complexity += 0.10
    except Exception:
        pass

    # ── Heuristic 4: Uncertainty trend ───────────────────────────────────
    try:
        _mdata = context.get("metrics_data", {})
        _trend = _mdata.get("uncertainty_trend", "stable")
        if _trend == "rising":
            complexity += 0.20
    except Exception:
        pass

    # ── Heuristic 5: Risk level signal ───────────────────────────────────
    if context.get("risk_level") == "HIGH" or context.get("high_risk"):
        complexity += 0.10

    complexity = min(1.0, round(complexity, 3))

    # Expected uncertainty scales non-linearly with complexity
    expected_uncertainty = round(min(1.0, complexity ** 0.75), 3)

    # Expected cost: cascade uses ~4× tokens vs fast path
    expected_cost = round(0.25 + complexity * 0.75, 3)

    return {
        "complexity": complexity,
        "expected_uncertainty": expected_uncertainty,
        "expected_cost": expected_cost,
        "matched_verbs": matched_verbs,
    }
