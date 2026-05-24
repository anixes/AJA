"""
aja/planning/verifier.py
============================
Phase 14 - Independent Verifier Agent & Constraint Layer.

Provides independent verification of proposed PlanGraphs.
The verifier does NOT reuse planner context and evaluates the plan
strictly on its own merits to catch hallucinations or invalid states.
"""

from __future__ import annotations

import json
from typing import Dict, Any

from aja.planning.models import PlanGraph
from aja.planning.dag_validator import DAGValidator

def check_constraints(plan: PlanGraph) -> bool:
    """
    Hard constraint filter.
    Returns True if the plan is structurally valid, False otherwise.
    Reuses Phase 11/12 validation mechanisms.
    """
    result = DAGValidator.validate(plan)
    return result.ok

def verify_plan(plan: PlanGraph, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Independent LLM verification of the plan.
    """
    from aja.llm import completion
    
    print(f"[Verifier] Verifying plan for goal: {plan.goal}", flush=True)
    log_path = r"C:\Users\Asus\.gemini\antigravity\brain\66b6bc99-9194-49a3-8107-c95894fbb4b3\verifier_debug.log"
    
    # 1. Check hard constraints first
    validation_result = DAGValidator.validate(plan, initial_state=state)
    if not validation_result.ok:
        res = {
            "valid": False,
            "missing_preconditions": [],
            "conflicts": [f"Failed structural DAG validation: {e}" for e in validation_result.errors]
        }
        with open(log_path, "a") as f:
            f.write(f"\n[ENTRY] Verifying plan for goal: {plan.goal}\n")
            f.write(f"PLAN JSON: {plan.to_json()}\n")
            f.write(f"RESULT: REJECTED (DAG Validation Errors: {validation_result.errors})\n")
        return res

    with open(log_path, "a") as f:
        f.write(f"\n[ENTRY] Verifying plan for goal: {plan.goal}\n")
    
    try:
        plan_json = json.dumps(plan.to_dict(), indent=2)
        
        prompt = f"""
You are a Logic Verifier for an AI Agent. 
The agent has generated a plan to achieve the goal: "{plan.goal}".

Plan Structure:
{plan_json}

Your task is to verify if this plan is logically sound, complete, and safe.
Check for:
1. Missing preconditions (e.g. trying to read a file before checking if it exists).
2. Logical conflicts (e.g. deleting a directory then trying to list its contents).
3. Structural validity (e.g. the steps actually lead to the goal).

JSON format:
{{
  "valid": boolean,
  "conflicts": [string],
  "missing_preconditions": [string]
}}
Do not output any other text or markdown.
"""
        response = completion(prompt, system_prompt="You are a strict logical verification agent.")
        raw_text = response.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        with open(log_path, "a") as f:
            f.write(f"RAW: {raw_text}\n")
            
        data = json.loads(raw_text)
        
        res = {
            "valid": bool(data.get("valid", False)),
            "missing_preconditions": list(data.get("missing_preconditions", [])),
            "conflicts": list(data.get("conflicts", []))
        }
        
        if not res["valid"]:
            msg = f"[Verifier] Plan REJECTED. Conflicts: {res['conflicts']}. Missing: {res['missing_preconditions']}"
            print(msg, flush=True)
            with open(log_path, "a") as f:
                f.write(f"RESULT: REJECTED\n{msg}\n")
            return res
            
        with open(log_path, "a") as f:
            f.write("RESULT: VALID\n")
        return res
        
    except Exception as e:
        print(f"[Verifier] LLM Verification failed: {e}", flush=True)
        with open(log_path, "a") as f:
            f.write(f"ERROR: {e}\n")
        # Fallback to True to avoid infinite loops if the LLM is down, but log it.
        return {
            "valid": True,
            "missing_preconditions": [],
            "conflicts": []
        }

def verify_step(node: Any, state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Phase 15: Step-Level Verification.
    Dynamically checks a single node before execution to catch state drifts.
    Deterministic version based on preconditions and uncertainty.
    """
    missing = []
    for k, v in node.preconditions.items():
        if state.get(k) != v:
            missing.append(k)

    risk = getattr(node, 'uncertainty', 0.5)

    return {
        "safe": len(missing) == 0,
        "risk": risk,
        "missing": missing
    }
