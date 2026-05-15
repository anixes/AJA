import json
import logging
import re
import pyarrow as pa
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from agentx.memory.manager import MemoryManager, get_memory_manager

logger = logging.getLogger("agent.decision.rules")

CAUSAL_ACTION_MAP = {
    "AUTH_ERROR":     "ASK",
    "RATE_LIMIT":     "RETRY_WITH_DELAY",
    "TOOL_NOT_FOUND": "REJECT",
    "INVALID_INPUT":  "ASK",
    "GENERAL":        "ASK",
}
FAILURE_THRESHOLD = 3
RULES_DECAY_DAYS = 60

_manager = get_memory_manager()

_RULES_SCHEMA = pa.schema([
    ("rule_id", pa.string()),
    ("pattern", pa.string()),
    ("condition_type", pa.string()),
    ("condition_payload_json", pa.string()),
    ("action", pa.string()),
    ("created_at", pa.string()),
])

def _init_rules_table():
    existing = _manager.db.list_tables()
    if hasattr(existing, "tables"):
        existing = existing.tables
    if "decision_rules" not in existing:
        _manager.db.create_table("decision_rules", schema=_RULES_SCHEMA)

_init_rules_table()

_AUTH_PATTERNS = re.compile(r"(authentication|auth|unauthorized|401|403|invalid.?api.?key|token.?expired|forbidden)", re.I)
_RATE_PATTERNS = re.compile(r"(rate.?limit|429|too.?many.?request|quota.?exceeded|throttl)", re.I)
_TOOL_PATTERNS = re.compile(r"(module.?not.?found|no.?module|tool.?not.?found|command.?not.?found|filenotfound|notimplemented)", re.I)
_INPUT_PATTERNS = re.compile(r"(invalid.?input|bad.?request|400|validation.?error|missing.?parameter)", re.I)


def classify_failure(error: str, result: str = "") -> str:
    combined = f"{error} {result}"
    if _AUTH_PATTERNS.search(combined): return "AUTH_ERROR"
    if _RATE_PATTERNS.search(combined): return "RATE_LIMIT"
    if _TOOL_PATTERNS.search(combined): return "TOOL_NOT_FOUND"
    if _INPUT_PATTERNS.search(combined): return "INVALID_INPUT"
    return "GENERAL"


def create_rule(pattern: str, condition_type: str, condition_payload: Dict[str, Any], action: str):
    import uuid
    now = datetime.now(timezone.utc).isoformat()
    try:
        table = _manager.db.open_table("decision_rules")
        existing = table.search().where(
            f"pattern = '{pattern}' AND condition_type = '{condition_type}' AND action = '{action}'"
        ).limit(1).to_list()
        if existing:
            return  # idempotent
        table.add([{
            "rule_id": uuid.uuid4().hex,
            "pattern": pattern,
            "condition_type": condition_type,
            "condition_payload_json": json.dumps(condition_payload),
            "action": action,
            "created_at": now
        }])
        logger.info("[Rules] RULE_CREATED_CAUSAL: pattern=%s condition_type=%s action=%s", pattern, condition_type, action)
        print(f"[Rules] RULE_CREATED_CAUSAL: '{pattern}' ({condition_type}) → {action}")
    except Exception as e:
        logger.error("Failed to create rule: %s", e)


def check_rules(objective: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    obj_lower = objective.lower()
    try:
        table = _manager.db.open_table("decision_rules")
        rules = table.to_arrow().to_pylist()
        for rule in reversed(rules):  # latest first
            if rule["pattern"].lower() not in obj_lower:
                continue
            condition_met = True
            payload_raw = rule.get("condition_payload_json", "{}")
            if payload_raw:
                payload = json.loads(payload_raw)
                for k, v in payload.items():
                    if context.get(k) != v:
                        condition_met = False
                        break
            if condition_met:
                action = rule["action"]
                ctype = rule["condition_type"]
                logger.info("[Rules] RULE_APPLIED_CAUSAL: pattern=%s action=%s", rule["pattern"], action)
                return {
                    "type": action,
                    "confidence": 1.0,
                    "reason": f"Causal rule override: '{rule['pattern']}' ({ctype})",
                    "evidence": [f"Rule matched: {rule['pattern']} ({ctype}) → {action}"]
                }
    except Exception as e:
        logger.error("Failed to check rules: %s", e)
    return None


def check_rules_for_failure(condition_type: str) -> Optional[str]:
    try:
        table = _manager.db.open_table("decision_rules")
        results = table.search().where(f"condition_type = '{condition_type}'").to_list()
        if results:
            return results[-1]["action"]  # latest rule
    except Exception as e:
        logger.error("Failed to lookup rule for failure: %s", e)
    return CAUSAL_ACTION_MAP.get(condition_type)


def extract_rule_from_failures(objective: str, context: Dict[str, Any], error: str = "", result: str = ""):
    from agentx.decision.feedback import extract_tags, get_feedback_stats
    stats = get_feedback_stats(objective)
    total_failures = sum(v.get("FAILURE", 0) for v in stats.values())
    if total_failures < FAILURE_THRESHOLD:
        return
    condition_type = classify_failure(error, result)
    action = CAUSAL_ACTION_MAP.get(condition_type, "ASK")
    tags = extract_tags(objective).split(",")
    pattern = tags[0] if tags else objective[:60]
    create_rule(pattern=pattern, condition_type=condition_type, condition_payload=context or {}, action=action)
