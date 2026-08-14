"""
Reflection & Strategy Learning: Native self-reflection memory for AJA Core.
Stores execution experiences, bias vectors, and strategy scores.
"""

knowledge_base = [
    {
        "pattern": "read-only exploration",
        "score": 0.9,
        "trusted": True,
        "executions": 5,
    },
    {
        "pattern": "parallel batch tasks",
        "score": 0.85,
        "trusted": True,
        "executions": 3,
    },
]

def process_execution(objective: str, plan: dict, result: dict) -> dict:
    """
    Reflects on goal execution outcome and updates strategy knowledge base.
    """
    success = result.get("success", False)
    entry = {
        "objective": objective,
        "success": success,
        "score": 1.0 if success else 0.0,
    }
    knowledge_base.append(entry)
    return entry
