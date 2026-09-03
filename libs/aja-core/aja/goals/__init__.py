"""
aja.goals — Goal formulation, SMART analysis, and autonomous execution.
"""

from aja.goals.analyzer import (
    GoalAnalyzer,
    HabitResult,
    ProgressResult,
    SmartValidationResult,
)
from aja.goals.goal_engine import Goal, GoalEngine

__all__ = [
    "Goal",
    "GoalEngine",
    "GoalAnalyzer",
    "SmartValidationResult",
    "ProgressResult",
    "HabitResult",
]
