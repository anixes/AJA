"""
agentx/decision/metrics.py
==========================
Phase 13 — Decision Quality & Performance Metrics.

Tracks per-decision-type performance over time so the engine can
make data-informed choices rather than purely LLM-driven ones.

Stored in the same SQLite DB as the rest of the decision layer.

Schema (decision_metrics table):
    id              INTEGER PK
    decision_type   TEXT          -- SKILL | COMPOSE | NEW | ASK | REJECT
    outcome         TEXT          -- TRUE_SUCCESS | FALSE_SUCCESS | PARTIAL_SUCCESS
                                  -- FAILURE | FALLBACK | SUCCESS
    attempts        INTEGER       -- number of retry attempts used (1-based)
    created_at      TIMESTAMP

Computed on-the-fly:
    decision_accuracy       = TRUE_SUCCESS / total (per type)
    retry_success_rate      = tasks that succeeded after >1 attempt / total retried
    failure_rate_by_type    = FAILURE / total (per type)
    avg_attempts_per_task   = avg(attempts)
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any

logger = logging.getLogger("agentx.decision.metrics")

DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))

# Outcomes that count as "true success" for accuracy calculations
TRUE_SUCCESS_OUTCOMES = {"TRUE_SUCCESS", "SUCCESS"}

# ---------------------------------------------------------------------------
# DB bootstrap
# ---------------------------------------------------------------------------

def _init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_metrics (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_type TEXT    NOT NULL,
                outcome       TEXT    NOT NULL,
                attempts      INTEGER NOT NULL DEFAULT 1,
                uncertainty_score REAL NOT NULL DEFAULT 0.0,
                created_at    TIMESTAMP NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dm_type ON decision_metrics(decision_type)"
        )
        
        # Phase 21 - Uncertainty Schema Migration
        try:
            conn.execute("ALTER TABLE decision_metrics ADD COLUMN uncertainty_score REAL NOT NULL DEFAULT 0.0")
        except Exception:
            pass # Columns already exist

        # Phase 18 Evaluation Metrics
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluation_metrics (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                false_success INTEGER NOT NULL,
                veto_triggered INTEGER NOT NULL,
                disagreement  INTEGER NOT NULL,
                created_at    TIMESTAMP NOT NULL
            )
        """)
        # Phase 19 Evaluator Reliability
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluator_performance (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                evaluator_id  TEXT NOT NULL,
                decision      TEXT NOT NULL,
                is_disagreement INTEGER NOT NULL,
                is_veto       INTEGER NOT NULL,
                ground_truth  TEXT,
                task_type     TEXT DEFAULT 'general',
                difficulty    TEXT DEFAULT 'medium',
                created_at    TIMESTAMP NOT NULL
            )
        """)
        # Phase 20 - Context Migration
        try:
            conn.execute("ALTER TABLE evaluator_performance ADD COLUMN task_type TEXT DEFAULT 'general'")
            conn.execute("ALTER TABLE evaluator_performance ADD COLUMN difficulty TEXT DEFAULT 'medium'")
        except Exception:
            pass # Columns already exist

        # Phase 25 - Routing Metrics
        conn.execute("""
            CREATE TABLE IF NOT EXISTS routing_metrics (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id               TEXT,
                routing_path          TEXT NOT NULL,
                predicted_complexity  REAL NOT NULL DEFAULT 0.0,
                predicted_uncertainty REAL NOT NULL DEFAULT 0.0,
                actual_uncertainty    REAL NOT NULL DEFAULT 0.0,
                actual_outcome        TEXT,
                created_at            TIMESTAMP NOT NULL
            )
        """)

# ---------------------------------------------------------------------------
# Phase 15 — Time-based decay
# ---------------------------------------------------------------------------

# Rows older than this are excluded from accuracy/scoring (not deleted).
DECAY_DAYS = 14

def _effective_rows() -> list:
    """Return rows within the decay window (recent observations only)."""
    try:
        _init_db()
        cutoff = _cutoff_timestamp(DECAY_DAYS)
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT decision_type, outcome, attempts, uncertainty_score FROM decision_metrics WHERE created_at >= ?",
                (cutoff,)
            ).fetchall()]
    except Exception as e:
        logger.error("[Metrics] Failed to read decayed metrics: %s", e)
        return []

def _cutoff_timestamp(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0) -
            timedelta(days=days)).isoformat()



# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def update_metrics(decision: Dict[str, Any], outcome: str, attempts: int = 1, uncertainty_score: float = 0.0):
    """
    Persist one metric record after a task completes.

    Parameters
    ----------
    decision : dict with at least {"type": ...}
    outcome  : evaluation string (TRUE_SUCCESS, FALSE_SUCCESS, PARTIAL_SUCCESS,
               FAILURE, FALLBACK, SUCCESS)
    attempts : how many retry attempts were consumed (default 1)
    uncertainty_score : Accumulated uncertainty for the task
    """
    try:
        _init_db()
        decision_type = decision.get("type", "NEW")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """INSERT INTO decision_metrics (decision_type, outcome, attempts, uncertainty_score, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (decision_type, outcome, attempts, uncertainty_score, datetime.now(timezone.utc).isoformat())
            )
        logger.info("[Metrics] METRICS_UPDATED: type=%s outcome=%s attempts=%d uncertainty=%.2f",
                    decision_type, outcome, attempts, uncertainty_score)
        logger.info("[Metrics] METRICS_RELIABILITY_UPDATED: Computed consistency metrics.")
    except Exception as e:
        logger.error("[Metrics] Failed to update metrics: %s", e)

def update_evaluation_metrics(false_success: bool, veto_triggered: bool, disagreement: bool):
    """Phase 18 — Track evaluation system health."""
    try:
        _init_db()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """INSERT INTO evaluation_metrics (false_success, veto_triggered, disagreement, created_at)
                   VALUES (?, ?, ?, ?)""",
                (1 if false_success else 0, 1 if veto_triggered else 0, 1 if disagreement else 0, datetime.now(timezone.utc).isoformat())
            )
    except Exception as e:
        logger.error("[Metrics] Failed to update eval metrics: %s", e)

def update_evaluator_performance(evaluator_id: str, decision: str, is_disagreement: bool, is_veto: bool, ground_truth: str = None, task_type: str = "general", difficulty: str = "medium"):
    """Phase 19 & 20 - Track individual evaluator performance with context."""
    try:
        _init_db()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """INSERT INTO evaluator_performance (evaluator_id, decision, is_disagreement, is_veto, ground_truth, task_type, difficulty, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (evaluator_id, decision, 1 if is_disagreement else 0, 1 if is_veto else 0, ground_truth, task_type, difficulty, datetime.now(timezone.utc).isoformat())
            )
    except Exception as e:
        logger.error("[Metrics] Failed to update evaluator performance: %s", e)

# ---------------------------------------------------------------------------
# Read / compute
# ---------------------------------------------------------------------------

def _all_rows() -> list:
    """Return all raw rows from decision_metrics."""
    try:
        _init_db()
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT decision_type, outcome, attempts FROM decision_metrics"
            ).fetchall()]
    except Exception as e:
        logger.error("[Metrics] Failed to read metrics: %s", e)
        return []


def get_metrics() -> Dict[str, Any]:
    """
    Compute and return all aggregate metrics.

    Returns
    -------
    {
        "per_type": {
            "SKILL": {
                "total": int,
                "true_success": int,
                "failure": int,
                "accuracy": float,        # true_success / total
                "failure_rate": float,
            },
            ...
        },
        "retry_success_rate": float,      # tasks that recovered after >1 attempt / all retried
        "avg_attempts": float,
        "total_tasks": int,
    }
    """
    rows = _effective_rows()   # Phase 15: decay window, not all-time
    if not rows:
        return {"per_type": {}, "retry_success_rate": 0.0, "avg_attempts": 1.0, "total_tasks": 0, "avg_uncertainty_per_task": 0.0, "uncertainty_to_failure_ratio": 0.0}

    per_type: Dict[str, Dict[str, Any]] = {}
    retried_total = 0
    retried_success = 0
    attempt_sum = 0
    uncertainty_sum = 0.0
    failures_with_uncertainty = 0.0
    total_failures = 0

    for row in rows:
        dtype = row["decision_type"]
        outcome = row["outcome"]
        attempts = row.get("attempts", 1) or 1
        u_score = row.get("uncertainty_score", 0.0) or 0.0
        
        uncertainty_sum += u_score

        attempt_sum += attempts
        if attempts > 1:
            retried_total += 1
            if outcome in TRUE_SUCCESS_OUTCOMES:
                retried_success += 1

        if dtype not in per_type:
            per_type[dtype] = {"total": 0, "true_success": 0, "failure": 0, "attempts_list": []}

        per_type[dtype]["total"] += 1
        per_type[dtype]["attempts_list"].append(attempts)
        if outcome in TRUE_SUCCESS_OUTCOMES:
            per_type[dtype]["true_success"] += 1
        if outcome == "FAILURE":
            per_type[dtype]["failure"] += 1
            total_failures += 1
            failures_with_uncertainty += u_score

    total_tasks = len(rows)
    avg_attempts = round(attempt_sum / total_tasks, 2) if total_tasks else 1.0
    avg_uncertainty = round(uncertainty_sum / total_tasks, 3) if total_tasks else 0.0
    u_to_f_ratio = round(failures_with_uncertainty / total_failures, 3) if total_failures else 0.0

    # Derive rates + reliability metrics (Phase 16)
    import math
    all_success_flags = []
    all_attempts = []
    for dtype, stats in per_type.items():
        t = stats["total"] or 1
        stats["accuracy"] = round(stats["true_success"] / t, 3)
        stats["failure_rate"] = round(stats["failure"] / t, 3)
        a_list = stats.pop("attempts_list", [])
        if len(a_list) > 1:
            mean_a = sum(a_list) / len(a_list)
            stats["retry_variance"] = round(
                sum((x - mean_a) ** 2 for x in a_list) / len(a_list), 3
            )
        else:
            stats["retry_variance"] = 0.0
        all_attempts.extend(a_list)
        # 1 per successful run, 0 per failure
        all_success_flags.extend(
            [1 if o in TRUE_SUCCESS_OUTCOMES else 0]
            * (stats["true_success"] + stats["failure"])
        )

    # Global success_variance (variance of binary success flags)
    if len(all_success_flags) > 1:
        mean_sf = sum(all_success_flags) / len(all_success_flags)
        success_variance = round(
            sum((x - mean_sf) ** 2 for x in all_success_flags) / len(all_success_flags), 4
        )
    else:
        success_variance = 0.0

    # consistency_score = accuracy adjusted downward by normalised retry variance
    if all_attempts and avg_attempts > 1:
        mean_a = sum(all_attempts) / len(all_attempts)
        retry_var = sum((x - mean_a) ** 2 for x in all_attempts) / len(all_attempts)
        penalty = min(0.3, retry_var / 10)   # cap penalty at 0.3
    else:
        penalty = 0.0
    base_accuracy = (
        sum(s["true_success"] for s in per_type.values()) / total_tasks
        if total_tasks else 0.0
    )
    consistency_score = round(max(0.0, base_accuracy - penalty), 3)

    # Phase 18 evaluation rates
    eval_rows = []
    try:
        cutoff = _cutoff_timestamp(DECAY_DAYS)
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            eval_rows = [dict(r) for r in conn.execute(
                "SELECT false_success, veto_triggered, disagreement FROM evaluation_metrics WHERE created_at >= ?",
                (cutoff,)
            ).fetchall()]
    except Exception as e:
        logger.error(f"[Metrics] Failed to read eval metrics: {e}")

    total_evals = len(eval_rows) or 1
    fs_rate = sum(r["false_success"] for r in eval_rows) / total_evals
    veto_freq = sum(r["veto_triggered"] for r in eval_rows) / total_evals
    disagreement_rate = sum(r["disagreement"] for r in eval_rows) / total_evals

    # Dynamic risk threshold: defaults to 0.5, but tightens (lowers) if false_success > 5%
    dynamic_risk_threshold = 0.5
    if fs_rate > 0.05:
        dynamic_risk_threshold = max(0.2, 0.5 - (fs_rate * 2))

    # Phase 24: Recent uncertainty window (last 20 tasks)
    try:
        cutoff_20 = _cutoff_timestamp(DECAY_DAYS)
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            recent_rows = [dict(r) for r in conn.execute(
                "SELECT uncertainty_score FROM decision_metrics WHERE created_at >= ? ORDER BY created_at DESC LIMIT 20",
                (cutoff_20,)
            ).fetchall()]
        avg_uncertainty_last_20 = round(
            sum(r["uncertainty_score"] for r in recent_rows) / len(recent_rows), 3
        ) if recent_rows else 0.0
    except Exception:
        avg_uncertainty_last_20 = avg_uncertainty

    # uncertainty_trend: "rising" | "stable" | "falling"
    uncertainty_trend = get_uncertainty_trend()

    return {
        "per_type": per_type,
        "retry_success_rate": round(retried_success / retried_total, 3) if retried_total else 0.0,
        "avg_attempts": avg_attempts,
        "total_tasks": total_tasks,
        # Phase 16 reliability fields
        "success_variance": success_variance,
        "consistency_score": consistency_score,
        # Phase 18 tracking
        "false_success_rate": round(fs_rate, 3),
        "veto_frequency": round(veto_freq, 3),
        "disagreement_rate": round(disagreement_rate, 3),
        "dynamic_risk_threshold": round(dynamic_risk_threshold, 3),
        # Phase 21 uncertainty tracking
        "avg_uncertainty_per_task": avg_uncertainty,
        "uncertainty_to_failure_ratio": u_to_f_ratio,
        # Phase 24 trend
        "avg_uncertainty_last_20": avg_uncertainty_last_20,
        "uncertainty_trend": uncertainty_trend,
    }


def get_uncertainty_trend() -> str:
    """
    Phase 24 — Compute uncertainty trend from recent decision history.

    Compares the average uncertainty of the latest 20 rows against the
    prior 20 rows (rows 21-40).  Returns one of:
        "rising"  — recent avg is > 10% higher than prior window
        "falling" — recent avg is > 10% lower than prior window
        "stable"  — within ±10%
    """
    try:
        _init_db()
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(
                "SELECT uncertainty_score FROM decision_metrics ORDER BY created_at DESC LIMIT 40"
            ).fetchall()]
        if len(rows) < 2:
            return "stable"
        recent = rows[:20]
        prior = rows[20:] if len(rows) > 20 else rows
        avg_recent = sum(r["uncertainty_score"] for r in recent) / len(recent)
        avg_prior = sum(r["uncertainty_score"] for r in prior) / len(prior)
        if avg_prior == 0:
            return "stable"
        delta = (avg_recent - avg_prior) / avg_prior
        if delta > 0.10:
            return "rising"
        if delta < -0.10:
            return "falling"
        return "stable"
    except Exception as e:
        logger.error("[Metrics] get_uncertainty_trend failed: %s", e)
        return "stable"


# ---------------------------------------------------------------------------
# Phase 25 — Routing Metrics
# ---------------------------------------------------------------------------

def update_routing_metrics(
    task_id: str,
    routing_path: str,
    predicted_complexity: float,
    predicted_uncertainty: float,
    actual_uncertainty: float = 0.0,
    actual_outcome: str = "",
) -> None:
    """
    Persist one routing observation.

    routing_path : "fast" | "cascade" | "aborted"
    """
    try:
        _init_db()
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO routing_metrics
                    (task_id, routing_path, predicted_complexity,
                     predicted_uncertainty, actual_uncertainty,
                     actual_outcome, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(task_id), routing_path,
                    round(predicted_complexity, 4),
                    round(predicted_uncertainty, 4),
                    round(actual_uncertainty, 4),
                    actual_outcome, now,
                ),
            )
    except Exception as e:
        logger.error("[Metrics] update_routing_metrics failed: %s", e)


def get_routing_accuracy() -> Dict[str, Any]:
    """
    Phase 25 — Return routing accuracy stats.

    Computes:
        avg_prediction_error    : mean |predicted - actual| uncertainty
        fast_path_rate          : fraction of tasks on fast path
        aborted_rate            : fraction aborted before execution
        per_path_outcomes       : outcome breakdown per routing path
    """
    try:
        _init_db()
        cutoff = _cutoff_timestamp(DECAY_DAYS)
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(
                "SELECT routing_path, predicted_uncertainty, actual_uncertainty, "
                "actual_outcome FROM routing_metrics WHERE created_at >= ?",
                (cutoff,)
            ).fetchall()]
    except Exception as e:
        logger.error("[Metrics] get_routing_accuracy failed: %s", e)
        return {}

    if not rows:
        return {}

    total = len(rows)
    errors = [abs(r["predicted_uncertainty"] - r["actual_uncertainty"]) for r in rows]
    avg_error = round(sum(errors) / total, 4)

    path_counts: Dict[str, int] = {}
    path_outcomes: Dict[str, Dict[str, int]] = {}
    for r in rows:
        p = r["routing_path"]
        path_counts[p] = path_counts.get(p, 0) + 1
        if p not in path_outcomes:
            path_outcomes[p] = {}
        outcome = r["actual_outcome"] or "UNKNOWN"
        path_outcomes[p][outcome] = path_outcomes[p].get(outcome, 0) + 1

    return {
        "total_routed": total,
        "avg_prediction_error": avg_error,
        "fast_path_rate": round(path_counts.get("fast", 0) / total, 3),
        "cascade_rate": round(path_counts.get("cascade", 0) / total, 3),
        "aborted_rate": round(path_counts.get("aborted", 0) / total, 3),
        "per_path_outcomes": path_outcomes,
    }


def get_evaluator_metrics() -> Dict[str, Any]:
    """Phase 19 - Retrieve per-evaluator reliability scores."""
    eval_rows = []
    try:
        cutoff = _cutoff_timestamp(DECAY_DAYS)
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            eval_rows = [dict(r) for r in conn.execute(
                "SELECT evaluator_id, decision, is_disagreement, is_veto, ground_truth FROM evaluator_performance WHERE created_at >= ?",
                (cutoff,)
            ).fetchall()]
    except Exception as e:
        logger.error(f"[Metrics] Failed to read evaluator performance: {e}")

    # Calculate per-evaluator metrics
    stats = {}
    for r in eval_rows:
        eid = r["evaluator_id"]
        if eid not in stats:
            stats[eid] = {"total": 0, "true_success": 0, "false_success": 0, "vetoes": 0, "disagreements": 0, "ground_truth_matches": 0, "ground_truth_total": 0}
        
        stats[eid]["total"] += 1
        
        if r["decision"] == "TRUE_SUCCESS":
            stats[eid]["true_success"] += 1
        elif r["decision"] == "FALSE_SUCCESS":
            stats[eid]["false_success"] += 1
            
        if r["is_veto"]:
            stats[eid]["vetoes"] += 1
        if r["is_disagreement"]:
            stats[eid]["disagreements"] += 1
            
        gt = r.get("ground_truth")
        if gt:
            stats[eid]["ground_truth_total"] += 1
            if gt == r["decision"]:
                stats[eid]["ground_truth_matches"] += 1

    result = {}
    for eid, s in stats.items():
        total = s["total"] or 1
        gt_total = s["ground_truth_total"] or 1
        
        # Reliability formula: relies on ground truth accuracy, or baseline behavior if untested
        gt_accuracy = s["ground_truth_matches"] / gt_total if s["ground_truth_total"] > 0 else 1.0
        false_success_rate = s["false_success"] / total
        disagreement_rate = s["disagreements"] / total
        veto_accuracy = s["vetoes"] / total # Simple proxy if no GT
        
        # Base reliability: high accuracy, low false success
        reliability = gt_accuracy * (1.0 - false_success_rate)
        
        result[eid] = {
            "success_rate": round(s["true_success"] / total, 3),
            "false_success_rate": round(false_success_rate, 3),
            "veto_accuracy": round(veto_accuracy, 3),
            "disagreement_rate": round(disagreement_rate, 3),
            "reliability": round(max(0.1, reliability), 3) # floor at 0.1
        }
    return result

def get_contextual_reliability(evaluator_id: str, context: Dict[str, str]) -> float:
    """Phase 20 - Context-Based Reliability Lookup"""
    task_type = context.get("task_type", "general")
    difficulty = context.get("difficulty", "medium")
    
    # 1. Try to get context-specific data
    try:
        cutoff = _cutoff_timestamp(DECAY_DAYS)
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(
                "SELECT decision, is_disagreement, is_veto, ground_truth FROM evaluator_performance WHERE evaluator_id = ? AND task_type = ? AND difficulty = ? AND created_at >= ?",
                (evaluator_id, task_type, difficulty, cutoff)
            ).fetchall()]
            
        if len(rows) >= 3: # Require at least a few samples to trust context
            stats = {"total": len(rows), "true_success": 0, "false_success": 0, "vetoes": 0, "disagreements": 0, "ground_truth_matches": 0, "ground_truth_total": 0}
            for r in rows:
                if r["decision"] == "TRUE_SUCCESS": stats["true_success"] += 1
                elif r["decision"] == "FALSE_SUCCESS": stats["false_success"] += 1
                if r["is_veto"]: stats["vetoes"] += 1
                if r["is_disagreement"]: stats["disagreements"] += 1
                gt = r.get("ground_truth")
                if gt:
                    stats["ground_truth_total"] += 1
                    if gt == r["decision"]: stats["ground_truth_matches"] += 1
            
            gt_accuracy = stats["ground_truth_matches"] / stats["ground_truth_total"] if stats["ground_truth_total"] > 0 else 1.0
            false_success_rate = stats["false_success"] / stats["total"]
            rel = gt_accuracy * (1.0 - false_success_rate)
            
            logger.info(f"[Metrics] CONTEXTUAL_RELIABILITY_USED for {evaluator_id} (context={task_type}/{difficulty}): {rel:.3f}")
            return max(0.1, rel)
            
    except Exception as e:
        logger.error(f"[Metrics] Failed to read contextual reliability: {e}")
        
    # 2. Fallback to global reliability
    all_metrics = get_evaluator_metrics()
    rel = all_metrics.get(evaluator_id, {}).get("reliability", 1.0)
    return rel

def detect_context_drift(evaluator_id: str, context: Dict[str, str]) -> bool:
    """Phase 20 - Context Drift Detection"""
    contextual_rel = get_contextual_reliability(evaluator_id, context)
    all_metrics = get_evaluator_metrics()
    global_rel = all_metrics.get(evaluator_id, {}).get("reliability", 1.0)
    
    if global_rel - contextual_rel > 0.3:
        logger.warning(f"[Metrics] CONTEXT_DRIFT_DETECTED: {evaluator_id} performs much worse in {context.get('task_type')} (diff={global_rel - contextual_rel:.2f})")
        return True
    return False

def get_metrics_summary_for_prompt() -> str:
    """
    Return a compact string suitable for injection into an LLM prompt.
    Influences (not enforces) the model's choice.
    """
    data = get_metrics()
    if not data["per_type"]:
        return ""

    lines = ["System Metrics (historical success rates — use as soft guidance only):"]
    for dtype, stats in sorted(data["per_type"].items(), key=lambda x: x[1]["accuracy"], reverse=True):
        pct = int(stats["accuracy"] * 100)
        fail_pct = int(stats["failure_rate"] * 100)
        lines.append(
            f"* {dtype}: {pct}% success rate, {fail_pct}% failure rate "
            f"(n={stats['total']})"
        )

    rsr = int(data["retry_success_rate"] * 100)
    lines.append(f"* Retry recovery rate: {rsr}% of retried tasks eventually succeed")
    lines.append(f"* Avg attempts per task: {data['avg_attempts']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI display
# ---------------------------------------------------------------------------

def print_metrics():
    """Pretty-print metrics for the `agentx metrics` command."""
    data = get_metrics()

    print("\n+----------------------------------------------+")
    print("|        AgentX Decision Quality Metrics       |")
    print("+----------------------------------------------+")
    print(f"  Total tasks tracked : {data['total_tasks']}")
    print(f"  Avg attempts/task   : {data['avg_attempts']}")
    print(f"  Retry success rate  : {int(data['retry_success_rate']*100)}%")
    print(f"  Success variance    : {data.get('success_variance', 0.0)}")
    print(f"  Consistency score   : {data.get('consistency_score', 0.0)}")

    if data["per_type"]:
        print("\n  Per-strategy breakdown:")
        print(f"  {'Strategy':<12} {'Success':>8} {'Failure':>8} {'Total':>7}")
        print("  " + "-" * 42)
        for dtype, stats in sorted(data["per_type"].items(),
                                   key=lambda x: x[1]["accuracy"], reverse=True):
            print(f"  {dtype:<12} {int(stats['accuracy']*100):>7}% "
                  f"{int(stats['failure_rate']*100):>7}% "
                  f"{stats['total']:>7}")

        # Top failing types
        failing = sorted(
            ((d, s) for d, s in data["per_type"].items() if s["failure"] > 0),
            key=lambda x: x[1]["failure"], reverse=True
        )
        if failing:
            print("\n  Top failing strategies:")
            for dtype, stats in failing[:3]:
                print(f"    {dtype}: {stats['failure']} failures "
                      f"({int(stats['failure_rate']*100)}% failure rate)")
    else:
        print("\n  No metric data yet — run some tasks first.")

    print("+----------------------------------------------+\n")
