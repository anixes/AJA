"""
agentx/decision/validator.py
===========================
Phase 10 — Deterministic Decision Validation.

Ensures LLM decisions are safe, valid, and satisfy system constraints
before execution begins. This module is purely deterministic.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger("agentx.decision.validator")

def validate_decision(decision: Dict[str, Any], context: Dict[str, Any]) -> str:
    """
    Validate an LLM-assisted decision against system rules.
    Returns: "VALID" | "OVERRIDE" | "REJECT"
    """
    dtype = decision.get("type")
    conf = float(decision.get("confidence", 0.0))
    risk = context.get("risk_level", "LOW")
    threshold = context.get("confidence_threshold", 0.6)

    # 1. High Risk Constraint
    if risk == "HIGH" and dtype not in ["ASK", "REJECT"]:
        logger.warning(f"[Validator] OVERRIDE: High risk objective requires ASK/REJECT. Original: {dtype}")
        decision["type"] = "ASK"
        decision["reason"] = f"Deterministic Override: HIGH risk objective requires human approval. (Original: {dtype})"
        return "OVERRIDE"

    # 2. Confidence Threshold Constraint
    if conf < threshold and dtype != "NEW":
        logger.warning(f"[Validator] OVERRIDE: Confidence {conf} below threshold {threshold}. Falling back to NEW.")
        decision["type"] = "NEW"
        decision["reason"] = f"Deterministic Override: Confidence {conf} below threshold {threshold}. (Original: {dtype})"
        return "OVERRIDE"

    # 3. SKILL Existence Constraint
    if dtype == "SKILL":
        top_skills = context.get("top_skills", [])
        if not top_skills:
            logger.warning("[Validator] OVERRIDE: Decision is SKILL but no matching skills found.")
            decision["type"] = "NEW"
            decision["reason"] = "Deterministic Override: No matching skills available."
            return "OVERRIDE"

    # 4. COMPOSE Constraints
    if dtype == "COMPOSE":
        # Heuristic: check if objective likely has multiple steps or if builder can handle it
        # Actually, Step 3 says "ensure >=2 steps" and "ensure all skills exist"
        # We'll use build_chain if possible to verify
        try:
            from agentx.skills.skill_composer import build_chain
            objective = context.get("objective", "")
            chain = build_chain(objective)
            if len(chain) < 2:
                logger.warning("[Validator] OVERRIDE: COMPOSE decision but build_chain found < 2 steps.")
                decision["type"] = "SKILL" if chain else "NEW"
                decision["reason"] = f"Deterministic Override: Less than 2 steps found for composition. (Falling back to {decision['type']})"
                return "OVERRIDE"
        except ImportError:
            # If composer not available, fallback
            decision["type"] = "NEW"
            decision["reason"] = "Deterministic Override: SkillComposer not available."
            return "OVERRIDE"

    # 5. Type Safety
    valid_types = ["SKILL", "COMPOSE", "NEW", "ASK", "REJECT"]
    if dtype not in valid_types:
        logger.error(f"[Validator] REJECT: Invalid decision type '{dtype}'")
        return "REJECT"

    return "VALID"
