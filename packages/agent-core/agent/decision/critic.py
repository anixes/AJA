from typing import List, Dict, Any
from agent.planning.models import PlanGraph

def critique_plan(plan: PlanGraph, state: dict) -> dict:
    """
    Returns structured critique of plan reasoning
    """
    issues = []

    for node in plan.nodes:
        # Missing preconditions
        for key, val in node.preconditions.items():
            if key not in state:
                issues.append({
                    "type": "missing_precondition",
                    "node": node.id,
                    "detail": f"{key} not in state"
                })

        # Logical gaps
        if not node.dependencies and node.preconditions:
            issues.append({
                "type": "logic_gap",
                "node": node.id,
                "detail": "has preconditions but no dependency source"
            })

        # Effect mismatch
        if not node.effects:
            issues.append({
                "type": "no_effect",
                "node": node.id
            })

    return {
        "issues": issues,
        "severity": len(issues)
    }

def llm_critique(plan: PlanGraph, state: dict) -> dict:
    """
    Perform deep reasoning analysis using an LLM.
    Detect logical inconsistencies, hidden assumptions, etc.
    """
    try:
        from agent.llm import completion
    except ImportError:
        # Mock LLM completion for testing
        def completion(prompt, system_prompt=""):
            return '```json\n{"issues": [{"type": "hidden_assumption", "node": "n1", "detail": "Mocked LLM assumption detected"}], "severity": 2}\n```'
    
    import json
    plan_json = json.dumps(plan.to_dict() if hasattr(plan, "to_dict") else str(plan))
    state_json = json.dumps(state)
    
    # OPTIMIZATION: Put State before Plan so llama.cpp can reuse the KV cache 
    # for the state across multiple plan evaluations.
    prompt = f"""
You are a Reasoning Critic for an AI Agent.
Analyze the current state and the proposed plan for logical inconsistencies or hidden assumptions.

State:
```json
{state_json}
```

Plan:
```json
{plan_json}
```

Return ONLY valid JSON with:
- "issues": list of {{"type": str, "node": str, "detail": str}}
- "severity": int (0-10)
"""
    try:
        response = completion(prompt, system_prompt="You are a strict reasoning critic.")
        raw_text = response.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        data = json.loads(raw_text.strip())
        return {
            "issues": data.get("issues", []),
            "severity": data.get("severity", 0)
        }
    except Exception as e:
        print(f"[Critic] LLM critique failed: {e}")
        return {"issues": [], "severity": 0}

def deep_critique(plan: PlanGraph, state: dict) -> dict:
    """
    Combines rule-based critique and LLM critique.
    Optimized for latency: Fast-fails if rule-based critique finds severe issues.
    """
    rule_crit = critique_plan(plan, state)
    
    # LATENCY OPTIMIZATION: Fast-fail
    # If the rule-based critic already found multiple issues, don't waste 
    # LLM inference time (and 128k context compute) on a broken plan.
    if rule_crit.get("severity", 0) >= 2:
        return rule_crit
        
    llm_crit = llm_critique(plan, state)
    
    issues = rule_crit.get("issues", []) + llm_crit.get("issues", [])
    severity = rule_crit.get("severity", 0) + llm_crit.get("severity", 0)
    
    return {
        "issues": issues,
        "severity": severity
    }


def compare_reasoning(plans: List[PlanGraph]) -> dict:
    """
    Detect shared assumptions and hidden errors
    """
    shared_patterns = {}
    
    for p in plans:
        for node in p.nodes:
            key = (node.task, tuple(node.dependencies))
            shared_patterns[key] = shared_patterns.get(key, 0) + 1

    escalation = None
    similarity_score = 0.0
    
    if len(plans) > 1:
        total_patterns = sum(shared_patterns.values())
        unique_patterns = len(shared_patterns)
        if total_patterns > 0 and unique_patterns > 0:
            similarity_score = (total_patterns - unique_patterns) / (unique_patterns * (len(plans) - 1))
            
    if similarity_score > 0.9:
        escalation = "OITL"
    elif similarity_score > 0.7:
        escalation = "verifier"
    elif similarity_score > 0.5:
        escalation = "replanner"

    return {
        "shared_patterns": shared_patterns,
        "similarity_score": similarity_score,
        "escalation": escalation
    }

def critic_score(plan: PlanGraph, critique: dict) -> float:
    base = 1.0
    penalty = len(critique["issues"]) * 0.1
    return max(0.0, base - penalty)
