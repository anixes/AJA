"""aja/decision/failure_analysis.py
=======================================
Phase 16 — Failure Attribution Layer. Now powered by LanceDB/Arrow.

Root-cause taxonomy:
    TOOL_ERROR      — the external tool / subprocess failed
    DECISION_ERROR  — the engine chose the wrong strategy
    REASONING_ERROR — the LLM produced malformed or contradictory output
    CONTEXT_ERROR   — missing, stale, or insufficient context

All rows are persisted to task_failures Arrow table.
Logs: FAILURE_ATTRIBUTED
"""

import re
import logging
import pyarrow as pa
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from aja.memory.manager import MemoryManager, get_memory_manager

logger = logging.getLogger("agent.decision.failure_analysis")
_manager = get_memory_manager()

_FAILURE_SCHEMA = pa.schema([
    ("failure_id", pa.string()),
    ("task_id", pa.string()),
    ("objective", pa.string()),
    ("root_cause", pa.string()),
    ("error_summary", pa.string()),
    ("result_summary", pa.string()),
    ("created_at", pa.string()),
])


def _init_failure_table():
    existing = _manager.db.list_tables()
    if hasattr(existing, "tables"):
        existing = existing.tables
    if "task_failures" not in existing:
        _manager.db.create_table("task_failures", schema=_FAILURE_SCHEMA)

_init_failure_table()

# ── Classifiers (pure regex, zero LLM) ──────────────────────────────────────
_TOOL_P    = re.compile(r"(subprocess|command not found|filenotfound|no such file|importerror|oserror|permissionerror|timeouterror|connectionerror)", re.I)
_DECISION_P = re.compile(r"(wrong strategy|skill mismatch|no matching skill|fallback triggered|incompatible.*skill)", re.I)
_REASONING_P = re.compile(r"(parse error|invalid json|malformed|json decode|unexpected.*output|response.*empty|model.*hallucin)", re.I)
_CONTEXT_P  = re.compile(r"(missing.*context|stale.*context|missing parameter|no objective|missing api key|keyerror|attributeerror)", re.I)


def classify_root_cause(error: str, result: str = "") -> str:
    combined = f"{error} {result}"
    if _CONTEXT_P.search(combined):   return "CONTEXT_ERROR"
    if _REASONING_P.search(combined): return "REASONING_ERROR"
    if _DECISION_P.search(combined):  return "DECISION_ERROR"
    if _TOOL_P.search(combined):      return "TOOL_ERROR"
    return "TOOL_ERROR"


def record_failure(task_id, objective: str, error: str, result: str = "", tracker=None) -> str:
    import uuid
    root_cause = classify_root_cause(error, result)
    try:
        table = _manager.db.open_table("task_failures")
        table.add([{
            "failure_id": uuid.uuid4().hex,
            "task_id": str(task_id) if task_id else "",
            "objective": (objective or "")[:500],
            "root_cause": root_cause,
            "error_summary": (error or "")[:500],
            "result_summary": (result or "")[:500],
            "created_at": datetime.now(timezone.utc).isoformat()
        }])
    except Exception as e:
        logger.error("[FailureAnalysis] Arrow write failed: %s", e)

    logger.info("[FailureAnalysis] FAILURE_ATTRIBUTED: task_id=%s root_cause=%s", task_id, root_cause)
    print(f"[FailureAnalysis] FAILURE_ATTRIBUTED: {root_cause} (task={task_id})")

    if tracker:
        try:
            tracker.log_event("FAILURE_ATTRIBUTED", {"task_id": task_id, "root_cause": root_cause, "error": (error or "")[:200]})
        except Exception:
            pass
    return root_cause


def get_failure_summary() -> Dict[str, Any]:
    try:
        table = _manager.db.open_table("task_failures")
        rows = table.to_arrow().to_pylist()
        summary: Dict[str, int] = {}
        for r in rows:
            rc = r["root_cause"]
            summary[rc] = summary.get(rc, 0) + 1
        return summary
    except Exception as e:
        logger.error("[FailureAnalysis] Failed to read summary: %s", e)
        return {}


def cleanup_old_failures(ttl_days: int = 30):
    """
    Prune task failure records older than ttl_days.
    """
    from datetime import timedelta
    try:
        table = _manager.db.open_table("task_failures")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
        table.delete(f"created_at < '{cutoff}'")
        print(f"[Maintenance] Pruned task failures older than {cutoff}")
    except Exception as e:
        logger.error("[FailureAnalysis] Failed to prune failures: %s", e)
