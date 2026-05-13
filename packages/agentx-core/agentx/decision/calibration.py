import json
import logging
import pyarrow as pa
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from agentx.memory.manager import MemoryManager, get_memory_manager
from agentx.decision.metrics import update_evaluator_performance

logger = logging.getLogger("agent.decision.calibration")

DRIFT_THRESHOLD = 0.30
_manager = get_memory_manager()

_GOLDEN_SCHEMA = pa.schema([
    ("golden_id", pa.string()),
    ("objective", pa.string()),
    ("result_text", pa.string()),
    ("expected_eval", pa.string()),
    ("last_actual", pa.string()),
    ("mismatch_count", pa.int32()),
    ("run_count", pa.int32()),
    ("created_at", pa.string()),
])


def _init_golden_table():
    existing = _manager.db.list_tables()
    if hasattr(existing, "tables"):
        existing = existing.tables
    if "golden_tasks" not in existing:
        _manager.db.create_table("golden_tasks", schema=_GOLDEN_SCHEMA)

_init_golden_table()

# Dynamic thresholds (in-memory, fast)
_thresholds = {
    "easy":        {"confidence": 0.5,  "reliability": 0.4},
    "medium":      {"confidence": 0.7,  "reliability": 0.5},
    "hard":        {"confidence": 0.85, "reliability": 0.6},
    "adversarial": {"confidence": 0.9,  "reliability": 0.7},
    "ambiguous":   {"confidence": 0.75, "reliability": 0.65},
    "default":     {"confidence": 0.6,  "reliability": 0.5},
}


def seed_golden_task(objective: str, result_text: str, expected_eval: str):
    import uuid
    valid = {"TRUE_SUCCESS", "PARTIAL_SUCCESS", "FALSE_SUCCESS"}
    if expected_eval not in valid:
        raise ValueError(f"expected_eval must be one of {valid}")
    table = _manager.db.open_table("golden_tasks")
    existing = table.search().where(
        f"objective = '{objective[:60]}' AND result_text = '{result_text[:60]}'"
    ).limit(1).to_list()
    if not existing:
        table.add([{
            "golden_id": uuid.uuid4().hex,
            "objective": objective,
            "result_text": result_text,
            "expected_eval": expected_eval,
            "last_actual": "",
            "mismatch_count": 0,
            "run_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        }])
        logger.info("[Calibration] Seeded golden task: expected=%s", expected_eval)


def run_calibration_tests(tracker=None) -> Dict[str, Any]:
    try:
        from agentx.decision.evaluator import evaluate_combined, EVALUATORS, evaluate_semantic
    except ImportError:
        logger.error("[Calibration] evaluate_combined not available — skipping.")
        return {"total": 0, "pass": 0, "fail": 0, "drift": False, "details": []}

    table = _manager.db.open_table("golden_tasks")
    tasks = table.to_arrow().to_pylist()
    if not tasks:
        return {"total": 0, "pass": 0, "fail": 0, "drift": False, "details": []}

    total = passed = failed = 0
    details = []

    for task in tasks:
        gid = task["golden_id"]
        objective = task["objective"]
        result_text = task["result_text"]
        expected = task["expected_eval"]
        ctx = {"objective": objective}
        try:
            actual = evaluate_combined(gid, result_text, ctx)
        except Exception as e:
            actual = "ERROR"
            logger.warning("[Calibration] evaluate_combined failed: %s", e)

        match = actual == expected
        total += 1
        passed += int(match)
        failed += int(not match)

        try:
            from agentx.decision.evaluator import get_evaluation_context
            eval_ctx = get_evaluation_context(objective, ctx)
        except Exception:
            eval_ctx = {"task_type": "general", "difficulty": "medium"}

        for evaluator in EVALUATORS:
            try:
                sem = evaluate_semantic(objective, result_text, ctx, stricter=False, model=evaluator)
                indiv = "TRUE_SUCCESS" if sem != "INCORRECT" else "FALSE_SUCCESS"
                update_evaluator_performance(evaluator, indiv, False, indiv == "FALSE_SUCCESS",
                                             ground_truth=expected, **eval_ctx)
            except Exception as e:
                logger.error("[Calibration] Evaluator calibration failed for %s: %s", evaluator, e)

        table.update(where=f"golden_id = '{gid}'", values={
            "last_actual": actual,
            "run_count": task["run_count"] + 1,
            "mismatch_count": task["mismatch_count"] + (0 if match else 1)
        })
        details.append({"objective": objective[:60], "expected": expected, "actual": actual, "pass": match})

    mismatch_rate = failed / total if total else 0.0
    drift = mismatch_rate > DRIFT_THRESHOLD
    if drift:
        logger.warning("[Calibration] EVALUATOR_DRIFT_DETECTED: %.0f%% mismatch", mismatch_rate * 100)
        print(f"[Calibration] EVALUATOR_DRIFT_DETECTED: {int(mismatch_rate*100)}% mismatch rate")
    return {"total": total, "pass": passed, "fail": failed,
            "mismatch_rate": round(mismatch_rate, 3), "drift": drift, "details": details}


def evaluate_evaluator() -> List[str]:
    from agentx.decision.metrics import get_evaluator_metrics
    issues = []
    for eid, stats in get_evaluator_metrics().items():
        if stats.get("reliability", 1.0) < 0.3:
            issues.append(f"WEAK_JUDGE_DETECTED: {eid}")
            logger.warning("[Calibration] WEAK_JUDGE_DETECTED: %s", eid)
        if stats.get("false_success_rate", 0.0) > 0.4:
            issues.append(f"BIAS_PATTERN (Yes-man): {eid}")
        if stats.get("disagreement_rate", 0.0) > 0.5:
            issues.append(f"INCONSISTENT_JUDGE: {eid}")
    return issues


def run_daily_calibration(tracker=None) -> Dict[str, Any]:
    logger.info("[Calibration] Running daily calibration...")
    results = run_calibration_tests(tracker=tracker)
    meta_issues = evaluate_evaluator()
    print(f"[Calibration] Daily calibration complete. Drift score: {results.get('mismatch_rate', 0):.3f}")
    return {**results, "meta_issues": meta_issues}


def compute_confidence_threshold(task_type: str = "default") -> dict:
    return _thresholds.get(task_type, _thresholds["default"])


def tune_threshold(task_type: str, false_positive_rate: float, false_negative_rate: float):
    if task_type not in _thresholds:
        return
    t = _thresholds[task_type]
    if false_positive_rate > 0.2:
        t["confidence"] = min(0.95, t["confidence"] + 0.05)
        t["reliability"] = min(0.9, t["reliability"] + 0.05)
    if false_negative_rate > 0.2:
        t["confidence"] = max(0.4, t["confidence"] - 0.05)
        t["reliability"] = max(0.3, t["reliability"] - 0.05)
