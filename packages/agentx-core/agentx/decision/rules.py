"""
agentx/decision/rules.py
========================
Phase 10 — Deterministic Rule Engine.
Phase 12 — Causal Failure-to-Rule Conversion.

Converts repeated, typed failures into targeted deterministic rules so that
the retry controller takes the *correct* corrective action rather than a
generic fallback.

Schema:
    decision_rules (id, pattern, condition_type, condition_payload, action, created_at)

condition_type values:
    AUTH_ERROR | RATE_LIMIT | TOOL_NOT_FOUND | INVALID_INPUT | GENERAL

action values:
    RETRY | ASK | REJECT | SWITCH_STRATEGY | RETRY_WITH_DELAY
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone
import os
import re
from typing import Dict, Any, Optional

logger = logging.getLogger("agentx.decision.rules")
DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))

# ---------------------------------------------------------------------------
# Causal failure → correct action mapping (Phase 12)
# ---------------------------------------------------------------------------
CAUSAL_ACTION_MAP = {
    "AUTH_ERROR":       "ASK",               # Auth is broken → ask human
    "RATE_LIMIT":       "RETRY_WITH_DELAY",  # Rate limited → retry with backoff
    "TOOL_NOT_FOUND":   "REJECT",            # Tool missing → reject cleanly
    "INVALID_INPUT":    "ASK",               # Bad input → ask for clarification
    "GENERAL":          "ASK",               # Unknown → safe default
}

# Minimum repeated failures before a rule is auto-created
FAILURE_THRESHOLD = 3

# Phase 15: rules older than this are not applied (soft decay via exclusion)
RULES_DECAY_DAYS = 60



# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------
def init_rules_db():
    """Ensure the decision_rules table exists with the Phase 12 schema."""
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_rules (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern           TEXT    NOT NULL,
                condition_type    TEXT    NOT NULL DEFAULT 'GENERAL',
                condition_payload TEXT,
                action            TEXT    NOT NULL,
                created_at        TIMESTAMP NOT NULL
            )
        """)
        # Migrate: add columns for DBs created before Phase 12
        for col_def in (
            "ALTER TABLE decision_rules ADD COLUMN condition_type TEXT NOT NULL DEFAULT 'GENERAL'",
            "ALTER TABLE decision_rules ADD COLUMN condition_payload TEXT",
        ):
            try:
                conn.execute(col_def)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Failure Classification (Phase 12 — Step 2)
# ---------------------------------------------------------------------------
_AUTH_PATTERNS = re.compile(
    r"(authentication|auth|unauthorized|401|403|invalid.?api.?key|api.?key|token.?expired|"
    r"forbidden|not.?authoriz)", re.I)
_RATE_PATTERNS = re.compile(
    r"(rate.?limit|429|too.?many.?request|quota.?exceeded|throttl)", re.I)
_TOOL_PATTERNS = re.compile(
    r"(module.?not.?found|no.?module|tool.?not.?found|command.?not.?found|"
    r"no.?such.?file|filenotfound|notimplemented)", re.I)
_INPUT_PATTERNS = re.compile(
    r"(invalid.?input|bad.?request|400|validation.?error|schema.?error|"
    r"missing.?parameter|unexpected.?argument)", re.I)


def classify_failure(error: str, result: str = "") -> str:
    """
    Classify a failure string into a typed condition.

    Parameters
    ----------
    error  : exception message or error string
    result : optional task result / stdout text for additional context

    Returns
    -------
    One of: AUTH_ERROR | RATE_LIMIT | TOOL_NOT_FOUND | INVALID_INPUT | GENERAL
    """
    combined = f"{error} {result}"

    if _AUTH_PATTERNS.search(combined):
        return "AUTH_ERROR"
    if _RATE_PATTERNS.search(combined):
        return "RATE_LIMIT"
    if _TOOL_PATTERNS.search(combined):
        return "TOOL_NOT_FOUND"
    if _INPUT_PATTERNS.search(combined):
        return "INVALID_INPUT"
    return "GENERAL"


# ---------------------------------------------------------------------------
# Rule CRUD
# ---------------------------------------------------------------------------
def create_rule(pattern: str, condition_type: str, condition_payload: Dict[str, Any], action: str):
    """
    Persist a deterministic rule.  De-duplicates on (pattern, condition_type, action).
    """
    init_rules_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            existing = conn.execute(
                "SELECT id FROM decision_rules WHERE pattern = ? AND condition_type = ? AND action = ?",
                (pattern, condition_type, action)
            ).fetchone()
            if existing:
                return  # idempotent

            conn.execute(
                """INSERT INTO decision_rules
                   (pattern, condition_type, condition_payload, action, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (pattern, condition_type, json.dumps(condition_payload), action, now)
            )
            logger.info("[Rules] RULE_CREATED_CAUSAL: pattern=%s condition_type=%s action=%s",
                        pattern, condition_type, action)
            print(f"[Rules] RULE_CREATED_CAUSAL: '{pattern}' ({condition_type}) → {action}")
    except Exception as e:
        logger.error("Failed to create rule: %s", e)


# ---------------------------------------------------------------------------
# Rule lookup
# ---------------------------------------------------------------------------
def check_rules(objective: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Deterministic rule engine — checks pattern substring and optional condition.
    No LLM is used.

    Returns an override decision dict, or None if no rule matched.
    """
    init_rules_db()
    obj_lower = objective.lower()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rules = conn.execute(
                """SELECT * FROM decision_rules
                   WHERE created_at >= datetime('now', '-' || ? || ' days')
                   ORDER BY id DESC""",
                (RULES_DECAY_DAYS,)
            ).fetchall()

            for rule in rules:
                pattern = rule["pattern"].lower()
                if pattern not in obj_lower:
                    continue

                # Evaluate optional condition_payload
                condition_met = True
                payload_raw = rule["condition_payload"]
                if payload_raw:
                    try:
                        payload = json.loads(payload_raw)
                        for k, v in payload.items():
                            if context.get(k) != v:
                                condition_met = False
                                break
                    except Exception:
                        pass

                if condition_met:
                    action = rule["action"]
                    ctype = rule["condition_type"] if "condition_type" in rule.keys() else "GENERAL"
                    logger.info("[Rules] RULE_APPLIED_CAUSAL: pattern=%s condition_type=%s action=%s",
                                pattern, ctype, action)
                    print(f"[Rules] RULE_APPLIED_CAUSAL: matched '{pattern}' ({ctype}) → {action}")
                    return {
                        "type": action,
                        "confidence": 1.0,
                        "reason": f"Causal rule override: '{pattern}' ({ctype})",
                        "evidence": [f"Rule matched: {pattern} ({ctype}) → {action}"]
                    }
    except Exception as e:
        logger.error("Failed to check rules: %s", e)

    return None


def check_rules_for_failure(condition_type: str) -> Optional[str]:
    """
    Look up the canonical action for a known failure type.
    Used directly by the retry controller.

    Returns the action string or None.
    """
    # First consult the persisted rules table for a specific match
    init_rules_db()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT action FROM decision_rules WHERE condition_type = ? ORDER BY id DESC LIMIT 1",
                (condition_type,)
            ).fetchone()
            if row:
                logger.info("[Rules] RULE_APPLIED_CAUSAL (failure lookup): %s → %s",
                            condition_type, row["action"])
                return row["action"]
    except Exception as e:
        logger.error("Failed to lookup rule for failure: %s", e)

    # Fall back to the in-memory causal map
    return CAUSAL_ACTION_MAP.get(condition_type)


# ---------------------------------------------------------------------------
# Causal Rule Extraction (Phase 12 — Step 3)
# ---------------------------------------------------------------------------
def extract_rule_from_failures(objective: str, context: Dict[str, Any],
                                error: str = "", result: str = ""):
    """
    Automatically called when repeated failures cross FAILURE_THRESHOLD.
    Classifies the failure and creates the *correct* causal rule.
    """
    from agentx.decision.feedback import extract_tags, get_feedback_stats

    # Determine how many times this objective has actually failed
    stats = get_feedback_stats(objective)
    total_failures = sum(v.get("FAILURE", 0) for v in stats.values())
    if total_failures < FAILURE_THRESHOLD:
        return  # Not enough evidence yet

    condition_type = classify_failure(error, result)
    action = CAUSAL_ACTION_MAP.get(condition_type, "ASK")

    # Use the most representative tag as the pattern so the rule generalises
    tags_str = extract_tags(objective)
    tags = [t for t in tags_str.split(",") if t]
    pattern = tags[0] if tags else objective[:60]

    create_rule(
        pattern=pattern,
        condition_type=condition_type,
        condition_payload=context or {},
        action=action,
    )
