"""
AJA Evals Package
=================
Replay-authoritative evaluation framework: score mission journal /
replay event streams against declarative EvalCases and run
baseline regression gates.
"""

from aja.evals.case import BUILTIN_CASES, EvalCase, RubricItem, get_case
from aja.evals.runner import (
    GateEntry,
    GateReport,
    run_case,
    run_regression_gate,
)
from aja.evals.scoring import EvalResult, score_events

__all__ = [
    "BUILTIN_CASES",
    "EvalCase",
    "RubricItem",
    "EvalResult",
    "GateEntry",
    "GateReport",
    "get_case",
    "run_case",
    "run_regression_gate",
    "score_events",
]
