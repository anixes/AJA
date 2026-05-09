"""agentx/decision/failure_analysis.py
=======================================
Phase 16 — Failure Attribution Layer.

classify_root_cause(error, result) returns a typed root-cause label so
feedback learning uses objective + root_cause rather than a generic
FAILURE bucket.

Root-cause taxonomy:
    TOOL_ERROR      — the external tool / subprocess failed
    DECISION_ERROR  — the engine chose the wrong strategy
    REASONING_ERROR — the LLM produced malformed or contradictory output
    CONTEXT_ERROR   — missing, stale, or insufficient context

All rows are persisted to task_failures for forensic querying.
Logs: FAILURE_ATTRIBUTED
"""

import sqlite3
import json
import logging
import re
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger("agentx.decision.failure_analysis")

DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))

# ---------------------------------------------------------------------------
# Root-cause classifiers (deterministic regex, no LLM)
# ---------------------------------------------------------------------------

_TOOL_PATTERNS = re.compile(
    r"(subprocess|command not found|filenotfound|no such file|tool not found|"
    r"module not found|importerror|oserror|permissionerror|timeouterror|"
    r"connectionerror|connectionrefused|httpx|requests\.exceptions)",
    re.I,
)
_DECISION_PATTERNS = re.compile(
    r"(wrong strategy|skill mismatch|skill not applicable|no matching skill|"
    r"fallback triggered|strategy.*failed|incompatible.*skill)",
    re.I,
)
_REASONING_PATTERNS = re.compile(
    r"(parse error|invalid json|malformed|json decode|unexpected.*output|"
    r"llm.*contradiction|response.*empty|model.*hallucin)",
    re.I,
)
_CONTEXT_PATTERNS = re.compile(
    r"(missing.*context|stale.*context|context.*invalid|missing parameter|"
    r"no objective|missing api key|missing.*config|keyerror|attributeerror)",
    re.I,
)


def classify_root_cause(error: str, result: str = "") -> str:
    """
    Return one of: TOOL_ERROR | DECISION_ERROR | REASONING_ERROR | CONTEXT_ERROR.

    Checks error first, then falls back to result text.
    Falls back to TOOL_ERROR if no pattern matches (most failures are tool-side).
    """
    combined = f"{error} {result}"

    if _CONTEXT_PATTERNS.search(combined):
        return "CONTEXT_ERROR"
    if _REASONING_PATTERNS.search(combined):
        return "REASONING_ERROR"
    if _DECISION_PATTERNS.search(combined):
        return "DECISION_ERROR"
    if _TOOL_PATTERNS.search(combined):
        return "TOOL_ERROR"

    # Default: if there is *any* error text, attribute to TOOL_ERROR
    if error.strip():
        return "TOOL_ERROR"
    return "TOOL_ERROR"   # safe generic fallback


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def _init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_failures (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id       INTEGER,
                objective     TEXT,
                root_cause    TEXT    NOT NULL,
                error_summary TEXT,
                result_summary TEXT,
                created_at    TIMESTAMP NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tf_root ON task_failures(root_cause)"
        )


def record_failure(
    task_id: Optional[int],
    objective: str,
    error: str,
    result: str = "",
    tracker=None,
) -> str:
    """
    Classify, persist, and log a task failure.

    Returns the root_cause string so callers can use it downstream
    (e.g. in feedback logging).
    Logs: FAILURE_ATTRIBUTED
    """
    root_cause = classify_root_cause(error, result)

    try:
        _init_db()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """INSERT INTO task_failures
                   (task_id, objective, root_cause, error_summary, result_summary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    objective[:500] if objective else "",
                    root_cause,
                    error[:500] if error else "",
                    result[:500] if result else "",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except Exception as e:
        logger.error("[FailureAnalysis] DB write failed: %s", e)

    logger.info(
        "[FailureAnalysis] FAILURE_ATTRIBUTED: task_id=%s root_cause=%s",
        task_id, root_cause
    )
    print(f"[FailureAnalysis] FAILURE_ATTRIBUTED: {root_cause} (task={task_id})")

    if tracker:
        try:
            tracker.log_event("FAILURE_ATTRIBUTED", {
                "task_id": task_id,
                "root_cause": root_cause,
                "error": error[:200] if error else "",
            })
        except Exception:
            pass

    return root_cause


def get_failure_summary() -> Dict[str, Any]:
    """Return counts by root_cause for the CLI / metrics display."""
    try:
        _init_db()
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT root_cause, COUNT(*) as cnt FROM task_failures GROUP BY root_cause"
            ).fetchall()
        return {row[0]: row[1] for row in rows}
    except Exception as e:
        logger.error("[FailureAnalysis] Failed to read summary: %s", e)
        return {}
