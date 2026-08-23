"""
AJA Evals: EvalCase Definition
==============================
Declarative evaluation cases scored against mission journal event streams
(replay-authoritative evaluation).
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

# A rubric item is either a declarative dict or a callable(events) -> bool.
RubricItem = Union[Dict[str, Any], Callable[[List[Dict[str, Any]]], bool]]


@dataclass
class EvalCase:
    """
    An evaluation case asserting properties over a mission's journal events.

    Attributes:
        name: Unique case identifier.
        objective: Human-readable description of what is being evaluated.
        required_event_types: Event types that MUST appear at least once.
        forbidden_event_types: Event types that must NOT appear (e.g. TOOL_FAILED).
        rubric: Extra assertions — callables or declarative dicts:
            {"type": "event_present", "event_type": ..., "where": "any"}
            {"type": "tool_succeeded", "tool": ...}
            {"type": "max_duration_ms", "value": ...}
            {"type": "output_contains", "text": ...}
        latency_budget_ms: Optional end-to-end budget (first -> last event).
    """

    name: str
    objective: str = ""
    required_event_types: List[str] = field(default_factory=list)
    forbidden_event_types: List[str] = field(default_factory=list)
    rubric: List[RubricItem] = field(default_factory=list)
    latency_budget_ms: Optional[int] = None


# Built-in reusable cases.
BUILTIN_CASES: Dict[str, EvalCase] = {
    "clean_run": EvalCase(
        name="clean_run",
        objective="Mission completed end-to-end without any tool failures.",
        required_event_types=[
            "MISSION_CREATED",
            "MISSION_RUN_STARTED",
            "MISSION_COMPLETED",
        ],
        forbidden_event_types=["TOOL_FAILED"],
    ),
    "planned_run": EvalCase(
        name="planned_run",
        objective="Mission produced a plan and completed cleanly.",
        required_event_types=[
            "MISSION_CREATED",
            "MISSION_RUN_STARTED",
            "MISSION_PLAN_GENERATED",
            "MISSION_COMPLETED",
        ],
        forbidden_event_types=["TOOL_FAILED"],
    ),
}


def get_case(name_or_case: Union[str, EvalCase]) -> EvalCase:
    """Resolves a case by name from BUILTIN_CASES or returns it directly."""
    if isinstance(name_or_case, EvalCase):
        return name_or_case
    key = str(name_or_case).strip().lower()
    if key not in BUILTIN_CASES:
        raise KeyError(
            f"Unknown eval case '{name_or_case}'. Available: {sorted(BUILTIN_CASES)}"
        )
    return BUILTIN_CASES[key]
