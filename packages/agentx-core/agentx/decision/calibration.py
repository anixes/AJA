"""
agentx/decision/calibration.py
================================
Phase 15 — Evaluator Calibration.

Maintains a golden_tasks table of expected-vs-actual evaluation pairs.
Runs calibration tests to detect evaluator drift — situations where the
evaluator consistently returns the wrong label for known inputs.

No LLM calls in the calibration runner itself: ground truth is provided
by the developer via seed_golden_task(). The LLM evaluator is only called
inside evaluate_task / evaluate_combined (which this module calls to test).

Logs:
    EVALUATOR_DRIFT_DETECTED  — mismatch rate exceeds DRIFT_THRESHOLD
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import os

logger = logging.getLogger("agentx.decision.calibration")

DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))
DRIFT_THRESHOLD = 0.30          # >30% mismatch rate = evaluator drift

# ---------------------------------------------------------------------------
# DB bootstrap
# ---------------------------------------------------------------------------

def _init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS golden_tasks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                objective       TEXT    NOT NULL,
                result_text     TEXT    NOT NULL,
                expected_eval   TEXT    NOT NULL,   -- TRUE_SUCCESS | PARTIAL_SUCCESS | FALSE_SUCCESS
                last_actual     TEXT,               -- last evaluation result recorded
                mismatch_count  INTEGER NOT NULL DEFAULT 0,
                run_count       INTEGER NOT NULL DEFAULT 0,
                created_at      TIMESTAMP NOT NULL
            )
        """)


# ---------------------------------------------------------------------------
# Step 1a — Seed golden tasks
# ---------------------------------------------------------------------------

def seed_golden_task(objective: str, result_text: str, expected_eval: str):
    """
    Register a known-good (or known-bad) evaluation pair.

    Parameters
    ----------
    objective      : canonical task objective string
    result_text    : the output the evaluator should see
    expected_eval  : ground truth — TRUE_SUCCESS | PARTIAL_SUCCESS | FALSE_SUCCESS
    """
    _init_db()
    valid = {"TRUE_SUCCESS", "PARTIAL_SUCCESS", "FALSE_SUCCESS"}
    if expected_eval not in valid:
        raise ValueError(f"expected_eval must be one of {valid}")
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute(
            "SELECT id FROM golden_tasks WHERE objective = ? AND result_text = ?",
            (objective, result_text)
        ).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO golden_tasks
                   (objective, result_text, expected_eval, created_at)
                   VALUES (?, ?, ?, ?)""",
                (objective, result_text, expected_eval,
                 datetime.now(timezone.utc).isoformat())
            )
            logger.info("[Calibration] Seeded golden task: expected=%s objective=%.60s",
                        expected_eval, objective)


# ---------------------------------------------------------------------------
# Step 1b — Run calibration
# ---------------------------------------------------------------------------

def run_calibration_tests(tracker=None) -> Dict[str, Any]:
    """
    Run the evaluator against all golden tasks and compare with expected output.
    Phase 19: Measures each individual evaluator to update their ground truth reliability.
    """
    _init_db()
    try:
        from agentx.decision.evaluator import evaluate_combined, EVALUATORS, evaluate_semantic
        from agentx.decision.metrics import update_evaluator_performance
    except ImportError:
        logger.error("[Calibration] evaluate_combined not available — skipping.")
        return {"total": 0, "pass": 0, "fail": 0, "drift": False, "details": []}

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        tasks = conn.execute(
            "SELECT * FROM golden_tasks"
        ).fetchall()

    if not tasks:
        return {"total": 0, "pass": 0, "fail": 0, "drift": False, "details": []}

    total, passed, failed = 0, 0, 0
    details = []

    for task in tasks:
        tid = task["id"]
        objective = task["objective"]
        result_text = task["result_text"]
        expected = task["expected_eval"]

        ctx = {"objective": objective}
        
        # Test the pipeline
        try:
            actual = evaluate_combined(tid, result_text, ctx)
        except Exception as e:
            actual = "ERROR"
            logger.warning("[Calibration] evaluate_combined failed for task %d: %s", tid, e)

        match = (actual == expected)
        total += 1
        if match:
            passed += 1
        else:
            failed += 1

        # Phase 19 & 20: Calibrate individual evaluators with context
        try:
            from agentx.decision.evaluator import get_evaluation_context
            eval_ctx = get_evaluation_context(objective, ctx)
        except Exception:
            eval_ctx = {"task_type": "general", "difficulty": "medium"}

        for evaluator in EVALUATORS:
            try:
                sem = evaluate_semantic(objective, result_text, ctx, stricter=False, model=evaluator)
                indiv_actual = "TRUE_SUCCESS"
                if sem == "INCORRECT":
                    indiv_actual = "FALSE_SUCCESS"
                elif sem == "PARTIAL":
                    indiv_actual = "PARTIAL_SUCCESS"
                
                is_veto = (indiv_actual == "FALSE_SUCCESS")
                update_evaluator_performance(evaluator, indiv_actual, False, is_veto, ground_truth=expected, task_type=eval_ctx["task_type"], difficulty=eval_ctx["difficulty"])
            except Exception as e:
                logger.error(f"[Calibration] Individual evaluator calibration failed for {evaluator}: {e}")

        # Update stats in DB
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """UPDATE golden_tasks
                   SET last_actual = ?,
                       run_count = run_count + 1,
                       mismatch_count = mismatch_count + ?
                   WHERE id = ?""",
                (actual, 0 if match else 1, tid)
            )

        details.append({
            "objective": objective[:60],
            "expected": expected,
            "actual": actual,
            "pass": match,
        })

    mismatch_rate = failed / total if total else 0.0
    drift = mismatch_rate > DRIFT_THRESHOLD

    if drift:
        logger.warning(
            "[Calibration] EVALUATOR_DRIFT_DETECTED: mismatch_rate=%.0f%% (threshold=%.0f%%)",
            mismatch_rate * 100, DRIFT_THRESHOLD * 100
        )
        print(f"[Calibration] EVALUATOR_DRIFT_DETECTED: {int(mismatch_rate*100)}% mismatch rate")
        if tracker:
            try:
                tracker.log_event("EVALUATOR_DRIFT_DETECTED", {
                    "mismatch_rate": round(mismatch_rate, 3),
                    "total": total,
                    "failed": failed,
                })
            except Exception:
                pass

    return {
        "total": total,
        "pass": passed,
        "fail": failed,
        "mismatch_rate": round(mismatch_rate, 3),
        "drift": drift,
        "details": details,
    }

def evaluate_evaluator() -> List[str]:
    """
    Phase 19 - Meta-Evaluation Layer
    Purpose:
    * detect inconsistent judges
    * detect drift
    * detect bias patterns
    """
    try:
        from agentx.decision.metrics import get_evaluator_metrics
        eval_metrics = get_evaluator_metrics()
    except Exception:
        return []
    
    issues = []
    for eid, stats in eval_metrics.items():
        rel = stats.get("reliability", 1.0)
        fs_rate = stats.get("false_success_rate", 0.0)
        dis_rate = stats.get("disagreement_rate", 0.0)
        
        if rel < 0.3:
            issues.append(f"WEAK_JUDGE_DETECTED: {eid} has reliability {rel}")
            logger.warning(f"[Calibration] WEAK_JUDGE_DETECTED: {eid} reliability={rel}")
        
        if fs_rate > 0.4:
            issues.append(f"BIAS_PATTERN (Yes-man): {eid} has false success rate {fs_rate}")
            logger.warning(f"[Calibration] BIAS_PATTERN: {eid} is biased towards false success.")
            
        if dis_rate > 0.5:
            issues.append(f"INCONSISTENT_JUDGE: {eid} disagrees too often ({dis_rate})")
            logger.warning(f"[Calibration] INCONSISTENT_JUDGE: {eid} disagreement rate is high.")

    if issues:
        logger.warning(f"[Calibration] Meta-Evaluation found issues: {issues}")
        
    return issues


# ---------------------------------------------------------------------------
# Step 8 — Daily Calibration Runner
# ---------------------------------------------------------------------------

def run_daily_calibration(tracker=None) -> Dict[str, Any]:
    """
    Run the daily calibration tests (replay golden tasks, compute drift score).
    If drift_score > threshold, EVALUATOR_DRIFT_DETECTED is logged.
    """
    logger.info("[Calibration] Running daily calibration...")
    results = run_calibration_tests(tracker=tracker)
    
    # Phase 19: Meta-evaluation
    meta_issues = evaluate_evaluator()
    
    drift_score = results.get("mismatch_rate", 0.0)
    print(f"[Calibration] Daily calibration complete. Drift score: {drift_score:.3f}")
    if meta_issues:
        print(f"[Calibration] Meta-Evaluation found {len(meta_issues)} issues. Check logs.")
    
    return {
        "drift_score": drift_score,
        "total": results.get("total", 0),
        "pass": results.get("pass", 0),
        "fail": results.get("fail", 0),
        "drift_detected": results.get("drift", False),
        "meta_issues": meta_issues
    }

# ---------------------------------------------------------------------------
# Phase 21.6: Dynamic Thresholding
# ---------------------------------------------------------------------------

_thresholds = {
    "easy": {"confidence": 0.5, "reliability": 0.4},
    "medium": {"confidence": 0.7, "reliability": 0.5},
    "hard": {"confidence": 0.85, "reliability": 0.6},
    "adversarial": {"confidence": 0.9, "reliability": 0.7},
    "ambiguous": {"confidence": 0.75, "reliability": 0.65},
    "default": {"confidence": 0.6, "reliability": 0.5}
}

def compute_confidence_threshold(task_type: str = "default") -> dict:
    """
    Returns dynamic thresholds based on task difficulty.
    """
    return _thresholds.get(task_type, _thresholds["default"])

def tune_threshold(task_type: str, false_positive_rate: float, false_negative_rate: float):
    """
    Adaptive tuning based on live error rates.
    If false positives are high, increase thresholds (stricter).
    If false negatives are high, decrease thresholds (more lenient).
    """
    if task_type not in _thresholds:
        return
        
    t = _thresholds[task_type]
    
    if false_positive_rate > 0.2:
        t["confidence"] = min(0.95, t["confidence"] + 0.05)
        t["reliability"] = min(0.9, t["reliability"] + 0.05)
        logger.info(f"[Calibration] Increased thresholds for {task_type} due to high FPR.")
        
    if false_negative_rate > 0.2:
        t["confidence"] = max(0.4, t["confidence"] - 0.05)
        t["reliability"] = max(0.3, t["reliability"] - 0.05)
        logger.info(f"[Calibration] Decreased thresholds for {task_type} due to high FNR.")

