"""
agentx/decision/feedback.py
==========================
Phase 10 — Decision Feedback Loop.

Tracks the outcomes of LLM-assisted decisions to enable self-improvement,
biasing, and better contextual prompting.
"""

import sqlite3
import hashlib
import os
from datetime import datetime, timezone

SECRETARY_DB = os.environ.get("AGENTX_DB_PATH", ".agentx/aja_secretary.sqlite3")

# Phase 15: decisions older than this are excluded from similarity lookups
FEEDBACK_DECAY_DAYS = 30


def init_feedback_db():
    """Ensure the decision_logs table exists."""
    os.makedirs(os.path.dirname(SECRETARY_DB) or ".", exist_ok=True)
    with sqlite3.connect(SECRETARY_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_logs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                objective_hash TEXT NOT NULL,
                decision_type  TEXT NOT NULL,
                confidence     REAL,
                outcome        TEXT NOT NULL, -- SUCCESS | FAILURE | FALLBACK
                task_id        INTEGER,
                created_at     TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_obj_hash ON decision_logs(objective_hash)")
        
        # Phase 10 - Long term memory extensions
        for col_def in (
            "ALTER TABLE decision_logs ADD COLUMN embedding BLOB",
            "ALTER TABLE decision_logs ADD COLUMN tags TEXT",
            "ALTER TABLE decision_logs ADD COLUMN original_objective TEXT"
        ):
            try:
                conn.execute(col_def)
            except Exception:
                pass

def get_objective_hash(objective: str) -> str:
    """Normalize and hash the objective string."""
    return hashlib.sha256(objective.strip().lower().encode('utf-8')).hexdigest()

def extract_tags(objective: str) -> str:
    """Extract simple keyword tags from an objective."""
    import re
    words = re.findall(r'\b\w+\b', objective.lower())
    stopwords = {"and", "the", "to", "a", "of", "for", "in", "on", "with", "is", "it"}
    tags = [w for w in words if len(w) > 3 and w not in stopwords]
    return ",".join(tags)

def log_decision_outcome(objective: str, decision_type: str, confidence: float, outcome: str, task_id: int = None):
    """Record the outcome of a decision."""
    try:
        init_feedback_db()
        obj_hash = get_objective_hash(objective)
        tags = extract_tags(objective)
        
        # --- Vectorization Phase 11 ---
        embedding_blob = None
        try:
            from agentx.orchestration.gateway import UnifiedGateway
            gateway = UnifiedGateway()
            # OpenRouter / OpenAI embeddings fallback
            emb = gateway.embed("llama_cpp:embedding", objective)
            if emb:
                import json
                embedding_blob = json.dumps(emb).encode('utf-8')
        except Exception as e:
            pass

        with sqlite3.connect(SECRETARY_DB) as conn:
            try:
                conn.execute("""
                    INSERT INTO decision_logs (objective_hash, decision_type, confidence, outcome, task_id, created_at, tags, original_objective, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (obj_hash, decision_type, confidence, outcome, task_id, datetime.now(timezone.utc).isoformat(), tags, objective, embedding_blob))
            except sqlite3.OperationalError:
                conn.execute("""
                    INSERT INTO decision_logs (objective_hash, decision_type, confidence, outcome, task_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (obj_hash, decision_type, confidence, outcome, task_id, datetime.now(timezone.utc).isoformat()))

        # Check for repeated failures and extract rule
        if outcome == "FAILURE":
            stats = get_feedback_stats(objective)
            if stats.get(decision_type, {}).get("FAILURE", 0) >= 3:
                try:
                    from agentx.decision.rules import extract_rule_from_failures
                    extract_rule_from_failures(objective, {})
                except Exception as ex:
                    print(f"[Feedback] Failed to extract rule: {ex}")
    except Exception as e:
        print(f"[Feedback] Failed to log outcome: {e}")

def get_recent_decisions(objective: str, limit: int = 10):
    """Retrieve recent decision outcomes for a specific objective hash."""
    try:
        init_feedback_db()
        obj_hash = get_objective_hash(objective)
        with sqlite3.connect(SECRETARY_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT decision_type, outcome, created_at 
                FROM decision_logs 
                WHERE objective_hash = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (obj_hash, limit)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[Feedback] Failed to retrieve history: {e}")
        return []

def get_similar_decisions(objective: str, limit: int = 10):
    """Retrieve past decisions with similar intent using vector embeddings or tags."""
    try:
        init_feedback_db()
        
        # --- Vectorization Phase 11 ---
        target_embedding = None
        try:
            from agentx.orchestration.gateway import UnifiedGateway
            gateway = UnifiedGateway()
            target_embedding = gateway.embed("llama_cpp:embedding", objective)
        except Exception:
            pass

        with sqlite3.connect(SECRETARY_DB) as conn:
            conn.row_factory = sqlite3.Row
            
            # 1. Try vector similarity if embedding succeeded (with decay window)
            if target_embedding:
                from datetime import timedelta
                _cutoff = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
                           - timedelta(days=FEEDBACK_DECAY_DAYS)).isoformat()
                rows = conn.execute("""
                    SELECT original_objective, decision_type, outcome, created_at, embedding 
                    FROM decision_logs 
                    WHERE embedding IS NOT NULL AND original_objective IS NOT NULL
                    AND created_at >= ?
                """, (_cutoff,)).fetchall()
                if rows:
                    import json
                    import math
                    results = []
                    for r in rows:
                        try:
                            emb = json.loads(r["embedding"].decode('utf-8'))
                            dot = sum(a*b for a, b in zip(target_embedding, emb))
                            norm_a = math.sqrt(sum(a*a for a in target_embedding))
                            norm_b = math.sqrt(sum(b*b for b in emb))
                            sim = dot / (norm_a * norm_b) if norm_a and norm_b else 0
                            results.append((sim, dict(r)))
                        except Exception:
                            continue
                    
                    results.sort(key=lambda x: x[0], reverse=True)
                    # Filter matches above 0.75 threshold
                    matches = [r[1] for r in results if r[0] > 0.75][:limit]
                    if matches:
                        return matches

            # 2. Fallback to tag matching
            tags = extract_tags(objective).split(',')
            tags = [t for t in tags if t]
            if not tags:
                return []
                
            clauses = []
            params = []
            for t in tags:
                clauses.append("tags LIKE ?")
                params.append(f"%{t}%")
                
            where_clause = " OR ".join(clauses)
            
            rows = conn.execute(f"""
                SELECT original_objective, decision_type, outcome, created_at 
                FROM decision_logs 
                WHERE ({where_clause}) AND original_objective IS NOT NULL
                AND created_at >= (
                    SELECT datetime('now', '-' || ? || ' days')
                )
                ORDER BY created_at DESC 
                LIMIT ?
            """, (*params, FEEDBACK_DECAY_DAYS, limit)).fetchall()
            return [dict(r) for r in rows]
            
    except Exception as e:
        print(f"[Feedback] Failed to get similar decisions: {e}")
        return []

def get_feedback_stats(objective: str):
    """Calculate success/failure stats for an objective."""
    history = get_recent_decisions(objective)
    stats = {}
    for entry in history:
        dtype = entry["decision_type"]
        if dtype not in stats:
            stats[dtype] = {"SUCCESS": 0, "FAILURE": 0, "FALLBACK": 0}
        stats[dtype][entry["outcome"]] += 1
    return stats
