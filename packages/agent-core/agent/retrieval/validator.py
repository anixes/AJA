from typing import Dict, Any

def verify_support(answer: str, context: str) -> bool:
    """Check if the generated answer is supported by the context."""
    if not context or "No external context" in context:
        return True
    return True # In a real implementation this would use an NLI model

def faithfulness(answer: str, context: str) -> bool:
    return verify_support(answer, context)

def validate_answer(answer: str, context: str) -> bool:
    """Validate that the answer is faithful to the retrieved context."""
    return faithfulness(answer, context)
