from datetime import datetime, timezone
from typing import Dict, Any, List
import json
import logging
import lancedb
import pyarrow as pa
from aja.memory.manager import MemoryManager, get_memory_manager

logger = logging.getLogger("agent.decision.metrics")
TRUE_SUCCESS_OUTCOMES = {"TRUE_SUCCESS", "SUCCESS"}
DECAY_DAYS = 14

_manager = get_memory_manager()

# ── Arrow Table Schemas ──────────────────────────────────────────────────────

_DECISION_METRICS_SCHEMA = pa.schema([
    ("decision_type", pa.string()),
    ("outcome", pa.string()),
    ("attempts", pa.int32()),
    ("uncertainty_score", pa.float32()),
    ("created_at", pa.string()),
])

_EVALUATION_METRICS_SCHEMA = pa.schema([
    ("false_success", pa.int32()),
    ("veto_triggered", pa.int32()),
    ("disagreement", pa.int32()),
    ("created_at", pa.string()),
])

_EVALUATOR_PERFORMANCE_SCHEMA = pa.schema([
    ("evaluator_id", pa.string()),
    ("decision", pa.string()),
    ("is_disagreement", pa.int32()),
    ("is_veto", pa.int32()),
    ("ground_truth", pa.string()),
    ("task_type", pa.string()),
    ("difficulty", pa.string()),
    ("created_at", pa.string()),
])

_ROUTING_METRICS_SCHEMA = pa.schema([
    ("task_id", pa.string()),
    ("routing_path", pa.string()),
    ("predicted_complexity", pa.float32()),
    ("predicted_uncertainty", pa.float32()),
    ("actual_uncertainty", pa.float32()),
    ("actual_outcome", pa.string()),
    ("created_at", pa.string()),
])


def _init_metrics_tables():
    db = _manager.db
    existing = db.list_tables()
    if hasattr(existing, "tables"):
        existing = existing.tables

    for name, schema in [
        ("decision_metrics", _DECISION_METRICS_SCHEMA),
        ("evaluation_metrics", _EVALUATION_METRICS_SCHEMA),
        ("evaluator_performance", _EVALUATOR_PERFORMANCE_SCHEMA),
        ("routing_metrics", _ROUTING_METRICS_SCHEMA),
    ]:
        if name not in existing:
            db.create_table(name, schema=schema)

_init_metrics_tables()


def _cutoff(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0) - timedelta(days=days)).isoformat()


def _read_table(name: str, cutoff: str = None) -> List[Dict]:
    try:
        table = _manager.db.open_table(name)
        if cutoff:
            return table.search().where(f"created_at >= '{cutoff}'").to_list()
        return table.to_arrow().to_pylist()
    except Exception as e:
        logger.error("[Metrics] Failed to read %s: %s", name, e)
        return []


# ── Write ────────────────────────────────────────────────────────────────────

def update_metrics(decision: Dict[str, Any], outcome: str, attempts: int = 1, uncertainty_score: float = 0.0):
    try:
        table = _manager.db.open_table("decision_metrics")
        table.add([{
            "decision_type": decision.get("type", "NEW"),
            "outcome": outcome,
            "attempts": attempts,
            "uncertainty_score": float(uncertainty_score),
            "created_at": datetime.now(timezone.utc).isoformat()
        }])
        logger.info("[Metrics] METRICS_UPDATED: type=%s outcome=%s attempts=%d uncertainty=%.2f",
                    decision.get("type"), outcome, attempts, uncertainty_score)
    except Exception as e:
        logger.error("[Metrics] Failed to update metrics: %s", e)


def update_evaluation_metrics(false_success: bool, veto_triggered: bool, disagreement: bool):
    try:
        table = _manager.db.open_table("evaluation_metrics")
        table.add([{
            "false_success": int(false_success),
            "veto_triggered": int(veto_triggered),
            "disagreement": int(disagreement),
            "created_at": datetime.now(timezone.utc).isoformat()
        }])
    except Exception as e:
        logger.error("[Metrics] Failed to update eval metrics: %s", e)


def update_evaluator_performance(evaluator_id: str, decision: str, is_disagreement: bool,
                                  is_veto: bool, ground_truth: str = None,
                                  task_type: str = "general", difficulty: str = "medium"):
    try:
        table = _manager.db.open_table("evaluator_performance")
        table.add([{
            "evaluator_id": evaluator_id,
            "decision": decision,
            "is_disagreement": int(is_disagreement),
            "is_veto": int(is_veto),
            "ground_truth": ground_truth or "",
            "task_type": task_type,
            "difficulty": difficulty,
            "created_at": datetime.now(timezone.utc).isoformat()
        }])
    except Exception as e:
        logger.error("[Metrics] Failed to update evaluator performance: %s", e)


def update_routing_metrics(task_id: str, routing_path: str, predicted_complexity: float,
                           predicted_uncertainty: float, actual_uncertainty: float = 0.0,
                           actual_outcome: str = ""):
    try:
        table = _manager.db.open_table("routing_metrics")
        table.add([{
            "task_id": str(task_id),
            "routing_path": routing_path,
            "predicted_complexity": round(predicted_complexity, 4),
            "predicted_uncertainty": round(predicted_uncertainty, 4),
            "actual_uncertainty": round(actual_uncertainty, 4),
            "actual_outcome": actual_outcome,
            "created_at": datetime.now(timezone.utc).isoformat()
        }])
    except Exception as e:
        logger.error("[Metrics] update_routing_metrics failed: %s", e)


# ── Read / Compute ───────────────────────────────────────────────────────────

def get_uncertainty_trend() -> str:
    try:
        rows = _read_table("decision_metrics")
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        if len(rows) < 2:
            return "stable"
        recent = rows[:20]
        prior = rows[20:40] if len(rows) > 20 else rows
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


def get_metrics() -> Dict[str, Any]:
    rows = _read_table("decision_metrics", _cutoff(DECAY_DAYS))
    if not rows:
        return {"per_type": {}, "retry_success_rate": 0.0, "avg_attempts": 1.0,
                "total_tasks": 0, "avg_uncertainty_per_task": 0.0, "uncertainty_to_failure_ratio": 0.0,
                "uncertainty_trend": "stable"}

    per_type: Dict[str, Dict] = {}
    retried_total = retried_success = attempt_sum = 0
    uncertainty_sum = failures_with_uncertainty = total_failures = 0.0

    for row in rows:
        dtype = row["decision_type"]
        outcome = row["outcome"]
        attempts = row.get("attempts", 1) or 1
        u_score = float(row.get("uncertainty_score", 0.0) or 0.0)
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
    for dtype, stats in per_type.items():
        t = stats["total"] or 1
        stats["accuracy"] = round(stats["true_success"] / t, 3)
        stats["failure_rate"] = round(stats["failure"] / t, 3)
        a_list = stats.pop("attempts_list", [])
        if len(a_list) > 1:
            mean_a = sum(a_list) / len(a_list)
            stats["retry_variance"] = round(sum((x - mean_a) ** 2 for x in a_list) / len(a_list), 3)
        else:
            stats["retry_variance"] = 0.0

    eval_rows = _read_table("evaluation_metrics", _cutoff(DECAY_DAYS))
    total_evals = len(eval_rows) or 1
    fs_rate = sum(r["false_success"] for r in eval_rows) / total_evals
    veto_freq = sum(r["veto_triggered"] for r in eval_rows) / total_evals
    disagreement_rate = sum(r["disagreement"] for r in eval_rows) / total_evals

    return {
        "per_type": per_type,
        "retry_success_rate": round(retried_success / retried_total, 3) if retried_total else 0.0,
        "avg_attempts": round(attempt_sum / total_tasks, 2) if total_tasks else 1.0,
        "total_tasks": total_tasks,
        "avg_uncertainty_per_task": round(uncertainty_sum / total_tasks, 3) if total_tasks else 0.0,
        "uncertainty_to_failure_ratio": round(failures_with_uncertainty / total_failures, 3) if total_failures else 0.0,
        "false_success_rate": round(fs_rate, 3),
        "veto_frequency": round(veto_freq, 3),
        "disagreement_rate": round(disagreement_rate, 3),
        "dynamic_risk_threshold": max(0.2, 0.5 - (fs_rate * 2)) if fs_rate > 0.05 else 0.5,
        "uncertainty_trend": get_uncertainty_trend(),
    }


def get_routing_accuracy() -> Dict[str, Any]:
    rows = _read_table("routing_metrics", _cutoff(DECAY_DAYS))
    if not rows:
        return {}
    total = len(rows)
    errors = [abs(r["predicted_uncertainty"] - r["actual_uncertainty"]) for r in rows]
    path_counts: Dict[str, int] = {}
    path_outcomes: Dict[str, Dict[str, int]] = {}
    for r in rows:
        p = r["routing_path"]
        path_counts[p] = path_counts.get(p, 0) + 1
        if p not in path_outcomes:
            path_outcomes[p] = {}
        outcome = r.get("actual_outcome") or "UNKNOWN"
        path_outcomes[p][outcome] = path_outcomes[p].get(outcome, 0) + 1
    return {
        "total_routed": total,
        "avg_prediction_error": round(sum(errors) / total, 4),
        "fast_path_rate": round(path_counts.get("fast", 0) / total, 3),
        "cascade_rate": round(path_counts.get("cascade", 0) / total, 3),
        "aborted_rate": round(path_counts.get("aborted", 0) / total, 3),
        "per_path_outcomes": path_outcomes,
    }


def get_evaluator_metrics() -> Dict[str, Any]:
    rows = _read_table("evaluator_performance", _cutoff(DECAY_DAYS))
    stats: Dict[str, Dict] = {}
    for r in rows:
        eid = r["evaluator_id"]
        if eid not in stats:
            stats[eid] = {"total": 0, "true_success": 0, "false_success": 0,
                          "vetoes": 0, "disagreements": 0, "gt_matches": 0, "gt_total": 0}
        stats[eid]["total"] += 1
        if r["decision"] == "TRUE_SUCCESS": stats[eid]["true_success"] += 1
        elif r["decision"] == "FALSE_SUCCESS": stats[eid]["false_success"] += 1
        if r["is_veto"]: stats[eid]["vetoes"] += 1
        if r["is_disagreement"]: stats[eid]["disagreements"] += 1
        if r.get("ground_truth"):
            stats[eid]["gt_total"] += 1
            if r["ground_truth"] == r["decision"]: stats[eid]["gt_matches"] += 1
    result = {}
    for eid, s in stats.items():
        total = s["total"] or 1
        gt_accuracy = s["gt_matches"] / s["gt_total"] if s["gt_total"] > 0 else 1.0
        fs_rate = s["false_success"] / total
        result[eid] = {
            "success_rate": round(s["true_success"] / total, 3),
            "false_success_rate": round(fs_rate, 3),
            "veto_accuracy": round(s["vetoes"] / total, 3),
            "disagreement_rate": round(s["disagreements"] / total, 3),
            "reliability": round(max(0.1, gt_accuracy * (1.0 - fs_rate)), 3)
        }
    return result


def get_contextual_reliability(evaluator_id: str, context: Dict[str, str]) -> float:
    task_type = context.get("task_type", "general")
    difficulty = context.get("difficulty", "medium")
    try:
        table = _manager.db.open_table("evaluator_performance")
        rows = table.search().where(
            f"evaluator_id = '{evaluator_id}' AND task_type = '{task_type}' AND difficulty = '{difficulty}'"
        ).to_list()
        if len(rows) >= 3:
            total = len(rows)
            fs = sum(1 for r in rows if r["decision"] == "FALSE_SUCCESS")
            gt_rows = [r for r in rows if r.get("ground_truth")]
            gt_acc = sum(1 for r in gt_rows if r["ground_truth"] == r["decision"]) / len(gt_rows) if gt_rows else 1.0
            return max(0.1, gt_acc * (1.0 - fs / total))
    except Exception as e:
        logger.error("[Metrics] Failed to read contextual reliability: %s", e)
    return get_evaluator_metrics().get(evaluator_id, {}).get("reliability", 1.0)


def detect_context_drift(evaluator_id: str, context: Dict[str, str]) -> bool:
    contextual_rel = get_contextual_reliability(evaluator_id, context)
    global_rel = get_evaluator_metrics().get(evaluator_id, {}).get("reliability", 1.0)
    if global_rel - contextual_rel > 0.3:
        logger.warning("[Metrics] CONTEXT_DRIFT_DETECTED: %s drift=%.2f", evaluator_id, global_rel - contextual_rel)
        return True
    return False


def get_metrics_summary_for_prompt() -> str:
    data = get_metrics()
    if not data["per_type"]:
        return ""
    lines = ["System Metrics (historical success rates — use as soft guidance only):"]
    for dtype, stats in sorted(data["per_type"].items(), key=lambda x: x[1]["accuracy"], reverse=True):
        lines.append(f"* {dtype}: {int(stats['accuracy']*100)}% success rate, "
                     f"{int(stats['failure_rate']*100)}% failure rate (n={stats['total']})")
    lines.append(f"* Retry recovery rate: {int(data['retry_success_rate']*100)}%")
    lines.append(f"* Avg attempts per task: {data['avg_attempts']}")
    return "\n".join(lines)


def print_metrics():
    data = get_metrics()
    print("\n+----------------------------------------------+")
    print("|        Agent Decision Quality Metrics       |")
    print("+----------------------------------------------+")
    print(f"  Total tasks tracked : {data['total_tasks']}")
    print(f"  Avg attempts/task   : {data['avg_attempts']}")
    print(f"  Retry success rate  : {int(data['retry_success_rate']*100)}%")
    if data["per_type"]:
        print("\n  Per-strategy breakdown:")
        for dtype, stats in sorted(data["per_type"].items(), key=lambda x: x[1]["accuracy"], reverse=True):
            print(f"  {dtype:<12} {int(stats['accuracy']*100):>7}% {int(stats['failure_rate']*100):>7}% {stats['total']:>7}")
    else:
        print("\n  No metric data yet — run some tasks first.")
    print("+----------------------------------------------+\n")
