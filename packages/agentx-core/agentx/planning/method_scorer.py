"""
agentx/planning/method_scorer.py
==================================
Phase 12 - Method Scoring & Metrics.

Implements two public functions:

  score_method(method)
    Computes a composite 0→1 quality score for a method based on
    historical performance metrics. Higher is better.

  update_metrics(method, success, latency, uncertainty)
    Applies an Exponentially Weighted Average (EWA) update after each
    plan execution and recomputes the score.

Scoring Formula
---------------
  score = (
      0.35 * success_rate
    + 0.20 * (1 - avg_uncertainty)
    + 0.15 * log(1 + reuse_count) / log(101)   # normalised at 100 uses
    + 0.15 * stability
    + 0.10 * (1 - clamp(avg_latency / max_latency, 0, 1))
    + 0.05 * parallelism_factor
  )

EWA update (alpha = 0.2):
  new_metric = alpha * new_value + (1 - alpha) * old_value
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict

# EWA smoothing factor (weight given to the latest observation)
_ALPHA: float = 0.20

# Normalisation denominator for reuse_count (at 100 uses score contribution maxes out)
_LOG_NORMALISER: float = math.log(1 + 100)

# Default maximum latency in seconds used for latency normalisation
DEFAULT_MAX_LATENCY: float = 60.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _parallelism_factor(method: Dict) -> float:
    """
    Fraction of nodes in the plan_template that have no dependencies.

    A higher fraction means more parallelism potential — a good proxy
    for execution efficiency.  Returns 0.5 as a safe default when the
    template is missing or empty.
    """
    template = method.get("plan_template", {})
    nodes = template.get("nodes", [])
    if not nodes:
        return 0.5
    independent = sum(1 for n in nodes if not n.get("dependencies"))
    return independent / len(nodes)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_method(method: Dict, max_latency: float = DEFAULT_MAX_LATENCY) -> float:
    """
    Compute a composite quality score in [0, 1] for ``method``.

    Parameters
    ----------
    method : dict
        A Phase 12 method entry (must contain a ``metrics`` sub-dict).
    max_latency : float
        Normalisation ceiling for the average latency component.

    Returns
    -------
    float in [0, 1].  Returns 0.4 as a neutral default if metrics are absent.
    """
    metrics = method.get("metrics")
    if not metrics:
        return 0.4  # neutral baseline for brand-new methods without metrics

    success_rate: float = _clamp(float(metrics.get("success_rate", 0.5)))
    avg_uncertainty: float = _clamp(float(metrics.get("avg_uncertainty", 0.5)))
    avg_latency: float = max(0.0, float(metrics.get("avg_latency", 0.0)))
    reuse_count: int = max(0, int(metrics.get("reuse_count", 0)))
    stability: float = _clamp(float(metrics.get("stability", 0.5)))

    reuse_component = math.log(1 + reuse_count) / _LOG_NORMALISER
    latency_component = 1.0 - _clamp(avg_latency / max(max_latency, 1.0))
    parallelism = _parallelism_factor(method)

    raw = (
        0.35 * success_rate
        + 0.20 * (1.0 - avg_uncertainty)
        + 0.15 * reuse_component
        + 0.15 * stability
        + 0.10 * latency_component
        + 0.05 * parallelism
    )
    return _clamp(raw)


def update_metrics(
    method: Dict,
    *,
    success: bool,
    latency: float,
    uncertainty: float,
    max_latency: float = DEFAULT_MAX_LATENCY,
) -> Dict:
    """
    Apply a single execution observation to the method's metrics using EWA.

    Parameters
    ----------
    method : dict
        Method entry to update (mutated in-place AND returned).
    success : bool
        Whether this execution succeeded.
    latency : float
        Wall-clock seconds the execution took.
    uncertainty : float
        Average uncertainty of the plan (0→1).
    max_latency : float
        Normalisation ceiling (passed through to score_method).

    Returns
    -------
    dict  — the updated method (same object).
    """
    metrics = method.setdefault("metrics", {})

    # Retrieve current values with safe defaults
    old_success_rate: float = _clamp(float(metrics.get("success_rate", 0.5)))
    old_uncertainty: float = _clamp(float(metrics.get("avg_uncertainty", 0.5)))
    old_latency: float = max(0.0, float(metrics.get("avg_latency", 0.0)))
    old_stability: float = _clamp(float(metrics.get("stability", 0.5)))
    reuse_count: int = max(0, int(metrics.get("reuse_count", 0)))

    # EWA updates
    success_val = 1.0 if success else 0.0
    metrics["success_rate"] = _clamp(_ALPHA * success_val + (1 - _ALPHA) * old_success_rate)
    metrics["avg_uncertainty"] = _clamp(_ALPHA * _clamp(uncertainty) + (1 - _ALPHA) * old_uncertainty)
    metrics["avg_latency"] = max(0.0, _ALPHA * max(0.0, latency) + (1 - _ALPHA) * old_latency)

    # Stability: incremental adjustment (not EWA — intentionally slower to change)
    if success:
        metrics["stability"] = _clamp(old_stability + 0.02)
    else:
        metrics["stability"] = _clamp(old_stability - 0.10)

    # Always increment reuse counter
    metrics["reuse_count"] = reuse_count + 1

    # Recompute composite score
    method["score"] = score_method(method, max_latency=max_latency)

    # Timestamp
    method["last_used"] = datetime.now(timezone.utc).isoformat()

    return method
