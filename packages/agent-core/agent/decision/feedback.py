import hashlib
import json
import logging
import pyarrow as pa
from datetime import datetime, timezone
from agent.memory.manager import MemoryManager, get_memory_manager

logger = logging.getLogger("agent.decision.feedback")
FEEDBACK_DECAY_DAYS = 30

_manager = get_memory_manager()

_FEEDBACK_SCHEMA = pa.schema([
    ("objective_hash", pa.string()),
    ("decision_type", pa.string()),
    ("confidence", pa.float32()),
    ("outcome", pa.string()),
    ("task_id", pa.string()),
    ("tags", pa.string()),
    ("original_objective", pa.string()),
    ("created_at", pa.string()),
    ("vector", pa.list_(pa.float32(), 1536)),  # For semantic similarity search
])


def _init_feedback_table():
    existing = _manager.db.list_tables()
    if hasattr(existing, "tables"):
        existing = existing.tables
    if "decision_logs" not in existing:
        _manager.db.create_table("decision_logs", schema=_FEEDBACK_SCHEMA)

_init_feedback_table()


def get_objective_hash(objective: str) -> str:
    return hashlib.sha256(objective.strip().lower().encode("utf-8")).hexdigest()


def extract_tags(objective: str) -> str:
    import re
    words = re.findall(r"\b\w+\b", objective.lower())
    stopwords = {"and", "the", "to", "a", "of", "for", "in", "on", "with", "is", "it"}
    return ",".join(w for w in words if len(w) > 3 and w not in stopwords)


def log_decision_outcome(objective: str, decision_type: str, confidence: float,
                         outcome: str, task_id=None):
    try:
        table = _manager.db.open_table("decision_logs")
        obj_hash = get_objective_hash(objective)
        tags = extract_tags(objective)
        # Zero vector; a real impl would call an embedding model here
        vector = [0.0] * 1536
        table.add([{
            "objective_hash": obj_hash,
            "decision_type": decision_type,
            "confidence": float(confidence),
            "outcome": outcome,
            "task_id": str(task_id) if task_id else "",
            "tags": tags,
            "original_objective": objective,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "vector": vector,
        }])
        if outcome == "FAILURE":
            stats = get_feedback_stats(objective)
            if stats.get(decision_type, {}).get("FAILURE", 0) >= 3:
                try:
                    from agent.decision.rules import extract_rule_from_failures
                    extract_rule_from_failures(objective, {})
                except Exception as ex:
                    print(f"[Feedback] Failed to extract rule: {ex}")
    except Exception as e:
        print(f"[Feedback] Failed to log outcome: {e}")


def get_recent_decisions(objective: str, limit: int = 10):
    try:
        table = _manager.db.open_table("decision_logs")
        obj_hash = get_objective_hash(objective)
        results = table.search().where(f"objective_hash = '{obj_hash}'").limit(limit).to_list()
        results.sort(key=lambda r: r["created_at"], reverse=True)
        return results
    except Exception as e:
        print(f"[Feedback] Failed to retrieve history: {e}")
        return []


def get_similar_decisions(objective: str, limit: int = 10):
    """Semantic similarity via LanceDB vector search."""
    try:
        table = _manager.db.open_table("decision_logs")
        # In a real impl, generate a vector here and call table.search(vector)
        # For now, fall back to tag-based match
        tags = extract_tags(objective).split(",")
        tags = [t for t in tags if t]
        results = []
        for tag in tags[:3]:
            rows = table.search().where(f"tags LIKE '%{tag}%'").limit(limit).to_list()
            results.extend(rows)
        seen = set()
        unique = []
        for r in results:
            key = r["objective_hash"]
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique[:limit]
    except Exception as e:
        print(f"[Feedback] Failed to get similar decisions: {e}")
        return []


def get_feedback_stats(objective: str):
    history = get_recent_decisions(objective)
    stats = {}
    for entry in history:
        dtype = entry["decision_type"]
        if dtype not in stats:
            stats[dtype] = {"SUCCESS": 0, "FAILURE": 0, "FALLBACK": 0}
        stats[dtype][entry.get("outcome", "FAILURE")] = \
            stats[dtype].get(entry.get("outcome", "FAILURE"), 0) + 1
    return stats


def cleanup_old_decisions(ttl_days: int = 30):
    """
    Prune decision logs older than ttl_days to maintain database performance.
    """
    from datetime import timedelta
    try:
        table = _manager.db.open_table("decision_logs")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
        table.delete(f"created_at < '{cutoff}'")
        print(f"[Maintenance] Pruned decision logs older than {cutoff}")
    except Exception as e:
        print(f"[Feedback] Failed to prune decision logs: {e}")
