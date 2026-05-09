"""
agentx/skills/skill_store.py
============================
Passive skill capture from successful task executions.

Phase 8A.1 + 8A.2 fixes applied:
  1. Normalized objective hash (stopword-stripped, sorted keywords)
  2. Skill versioning — tool_sequence change → new version in same family
  3. skill_sources table — back-link to originating task_ids
  4. Token-overlap search — replaces fragile LIKE matching
  5. (8A.2) Intent tags via keyword→category mapping
  6. (8A.2) MIN_TOOL_STEPS=2 gate — prevents skill explosion on trivial tasks
  7. (8A.2) when_to_use / pitfalls / prerequisites — rule-based applicability signal

Design constraints (never violate):
  - Observer only — never alters execution flow or SwarmEngine.
  - Captures only COMPLETED, first-pass tasks (retry_count == 0,
    every tool execution COMPLETED).

Public API
----------
  create_skill_from_task(task_id)        -> str | None  (skill_id)
  list_skills(limit)                     -> list[dict]
  search_skills(query, limit)            -> list[dict]  (token-overlap ranked)
  get_skill(skill_id)                    -> dict | None
  get_skill_sources(skill_id)            -> list[dict]  (originating tasks)
"""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# DB path — resolved at call-time so AGENTX_DB_PATH overrides after import
# ---------------------------------------------------------------------------

def _db_path() -> str:
    return os.environ.get(
        "AGENTX_DB_PATH",
        os.path.join(".agentx", "aja_secretary.sqlite3"),
    )


# ---------------------------------------------------------------------------
# Stopwords (shared by hash normalization AND search tokenizer)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "is", "it", "to", "in", "of", "for", "and", "or",
    "with", "that", "this", "be", "was", "are", "by", "at", "from", "as",
    "on", "but", "not", "all", "if", "so", "do", "we", "can", "will",
    "get", "set", "use", "run", "make", "add", "now", "new", "its",
}

# Minimum tool steps to qualify as a reusable skill (prevents skill explosion)
MIN_TOOL_STEPS = 2

# ── Scoring weights (Issue 1 — false positive prevention) ─────────────────────
# input_pattern is the ground-truth objective and must always gate the match.
# Secondary fields (name, description) provide a small boost only.
_IP_WEIGHT  = 0.75    # weight for input_pattern tier
_SEC_WEIGHT = 0.25    # max weight for secondary fields

# ── Confidence gate (Issue 3) ─────────────────────────────────────────────────
CONFIDENCE_GATE = 0.6    # default minimum confidence for recommend_skill()

# ── Risk classification rules (Issue 2) ───────────────────────────────────────
# Evaluated in priority order; first match wins.
_RISK_RULES: list = [
    # HIGH: payment, destructive, credential-access
    ({"payment", "charge", "billing", "invoice", "stripe", "paypal", "credit",
      "refund", "transaction"}, "HIGH"),
    ({"delete", "remove", "purge", "drop", "truncate", "destroy", "wipe"}, "HIGH"),
    ({"password", "secret", "credential", "private", "encrypt", "decrypt",
      "apikey", "privatekey"}, "HIGH"),
    # MEDIUM: outbound comms, deployment, auth tokens
    ({"email", "sms", "notify", "send", "broadcast", "alert", "webhook"}, "MEDIUM"),
    ({"deploy", "release", "rollout", "promote", "production", "prod"}, "MEDIUM"),
    ({"auth", "authenticate", "authorize", "oauth", "token", "login"}, "MEDIUM"),
    # LOW: everything else
]

# Keyword → intent category map (Issue 2 — intent tags)
_INTENT_MAP: dict = {
    # Communication
    "email": "communication", "send": "communication", "notify": "communication",
    "message": "communication", "alert": "communication", "sms": "communication",
    "broadcast": "communication",
    # Data retrieval
    "fetch": "data-retrieval", "download": "data-retrieval", "query": "data-retrieval",
    "read": "data-retrieval", "extract": "data-retrieval", "pull": "data-retrieval",
    "retrieve": "data-retrieval", "load": "data-retrieval",
    # Storage / persistence
    "save": "storage", "write": "storage", "store": "storage", "upload": "storage",
    "persist": "storage", "insert": "storage", "backup": "storage",
    # Data processing
    "analyze": "analysis", "analyse": "analysis", "summarize": "analysis",
    "process": "processing", "transform": "processing", "parse": "processing",
    "calculate": "analysis", "compute": "analysis", "aggregate": "analysis",
    # Deployment / release
    "deploy": "deployment", "release": "deployment", "publish": "deployment",
    "launch": "deployment", "rollout": "deployment", "promote": "deployment",
    # Security / auth / validation
    "auth": "security", "authenticate": "security", "authorize": "security",
    "encrypt": "security", "decrypt": "security", "token": "security",
    "validate": "validation", "verify": "validation", "check": "validation",
    # Cleanup
    "delete": "cleanup", "remove": "cleanup", "clean": "cleanup",
    "purge": "cleanup", "archive": "cleanup",
    # Monitoring / reporting
    "report": "reporting", "dashboard": "reporting", "log": "monitoring",
    "monitor": "monitoring", "watch": "monitoring", "track": "monitoring",
    "observe": "monitoring", "audit": "monitoring",
    # Code quality
    "refactor": "code-improvement", "optimize": "code-improvement",
    "improve": "code-improvement", "migrate": "code-improvement",
    "upgrade": "code-improvement", "lint": "code-improvement",
    # Integration
    "sync": "integration", "integrate": "integration", "connect": "integration",
    "import": "integration", "export": "integration", "map": "integration",
}

# ---------------------------------------------------------------------------
# Issue 4 — Synonym expansion (recall improvement, no embeddings needed)
# ---------------------------------------------------------------------------
# Each key expands to its synonyms during query tokenization.
# Directional: query tokens are expanded; skill tokens are not (avoids false positives).
_SYNONYMS: dict = {
    # fetch / retrieve family
    "fetch":    {"download", "retrieve", "pull", "get", "load", "read"},
    "download": {"fetch", "retrieve", "pull", "get"},
    "retrieve": {"fetch", "download", "pull", "get", "load"},
    "load":     {"read", "fetch", "retrieve", "import"},
    "get":      {"fetch", "retrieve", "read", "download"},
    # send / notify family
    "send":     {"email", "notify", "dispatch", "transmit", "deliver", "push"},
    "notify":   {"send", "alert", "email", "message"},
    "email":    {"send", "notify", "mail"},
    # store / save family
    "store":    {"save", "write", "persist", "insert", "upload"},
    "save":     {"store", "write", "persist", "backup"},
    "write":    {"store", "save", "persist", "export"},
    # delete / remove family
    "delete":   {"remove", "purge", "drop", "wipe", "erase"},
    "remove":   {"delete", "purge", "drop"},
    "purge":    {"delete", "remove", "wipe"},
    # process / analyse family
    "process":  {"analyze", "transform", "parse", "compute"},
    "analyze":  {"process", "analyse", "compute", "inspect", "evaluate"},
    "analyse":  {"analyze", "process", "compute"},
    "parse":    {"process", "transform", "decode"},
    # generate / create family
    "generate": {"create", "build", "produce", "make", "render"},
    "create":   {"generate", "build", "produce", "add", "insert"},
    "build":    {"generate", "create", "compile", "construct"},
    # deploy / release family
    "deploy":   {"release", "publish", "rollout", "launch", "promote"},
    "release":  {"deploy", "publish", "launch"},
    "publish":  {"deploy", "release", "distribute"},
    # search / query family
    "search":   {"query", "find", "lookup", "filter", "list"},
    "query":    {"search", "find", "filter", "select"},
    "find":     {"search", "query", "lookup"},
    # update / modify family
    "update":   {"modify", "edit", "change", "patch", "amend"},
    "modify":   {"update", "edit", "change", "patch"},
    "edit":     {"update", "modify", "change"},
    # validate / check family
    "validate": {"verify", "check", "confirm", "test", "assert"},
    "verify":   {"validate", "check", "confirm"},
    "check":    {"validate", "verify", "test", "inspect"},
    # document family
    "document":  {"file", "report", "record", "artifact"},
    "report":    {"document", "summary", "record", "output"},
    "file":      {"document", "record", "artifact"},
    # charge / payment family
    "charge":   {"payment", "bill", "invoice", "transaction"},
    "payment":  {"charge", "bill", "invoice", "transaction"},
    # config / settings family
    "config":   {"configuration", "settings", "options", "setup", "configure"},
    "configure": {"config", "setup", "settings", "initialize"},
    "settings": {"config", "configuration", "options"},
}


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

def _init_skill_db(conn: sqlite3.Connection) -> None:
    # Main skills table — includes version, family_id, and applicability fields
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id               TEXT PRIMARY KEY,
            family_id        TEXT NOT NULL,        -- hash of normalized objective (no tool shape)
            version          INTEGER NOT NULL DEFAULT 1,
            name             TEXT NOT NULL,
            description      TEXT,
            input_pattern    TEXT,
            tags             TEXT,                 -- JSON array (keywords + intent categories)
            tool_sequence    TEXT NOT NULL,        -- JSON [{tool_name, args_schema}]
            when_to_use      TEXT,                 -- conditions under which skill applies
            pitfalls         TEXT,                 -- known failure modes
            prerequisites    TEXT,                 -- JSON list of required conditions
            risk_level       TEXT NOT NULL DEFAULT 'LOW', -- LOW | MEDIUM | HIGH
            success_count    INTEGER NOT NULL DEFAULT 1,
            failure_count    INTEGER NOT NULL DEFAULT 0,
            confidence_score REAL    NOT NULL DEFAULT 1.0,
            created_at       TIMESTAMP NOT NULL,
            updated_at       TIMESTAMP NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_family  ON skills (family_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_version ON skills (family_id, version DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_conf    ON skills (confidence_score DESC)")

    # Migrate existing rows: add columns if not present (handles old DBs)
    for col_def in (
        "ALTER TABLE skills ADD COLUMN family_id        TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE skills ADD COLUMN version          INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE skills ADD COLUMN confidence_score REAL    NOT NULL DEFAULT 1.0",
        "ALTER TABLE skills ADD COLUMN tags             TEXT",
        "ALTER TABLE skills ADD COLUMN when_to_use      TEXT",
        "ALTER TABLE skills ADD COLUMN pitfalls         TEXT",
        "ALTER TABLE skills ADD COLUMN prerequisites    TEXT",
        "ALTER TABLE skills ADD COLUMN risk_level       TEXT NOT NULL DEFAULT 'LOW'",
    ):
        try:
            conn.execute(col_def)
        except sqlite3.OperationalError:
            pass

    # Audit / back-link table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_sources (
            skill_id    TEXT    NOT NULL,
            task_id     INTEGER NOT NULL,
            version     INTEGER NOT NULL DEFAULT 1,
            captured_at TIMESTAMP NOT NULL,
            PRIMARY KEY (skill_id, task_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_skill ON skill_sources (skill_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_task  ON skill_sources (task_id)")


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    _init_skill_db(conn)
    return conn


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set:
    """Lowercase alpha tokens ≥3 chars, stopwords removed."""
    words = re.findall(r'\b[a-z]{3,}\b', text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _expand_tokens(tokens: set) -> set:
    """
    Issue 4 — Recall improvement: expand a token set with synonyms.

    Only applied to QUERY tokens (one-directional), never to skill tokens.
    This avoids false positives while improving recall for paraphrased queries.

    Example:
        'fetch document' expands to include 'download', 'retrieve', 'file', 'report' …
        → now matches a skill whose input_pattern says 'download file'
    """
    expanded = set(tokens)  # start with original tokens
    for token in tokens:
        expanded.update(_SYNONYMS.get(token, set()))
    return expanded


def _normalize_objective(objective: str) -> str:
    """
    Sorted keyword string for stable hashing.
    'Download the file' == 'file download' == 'the file download'
    """
    return " ".join(sorted(_tokenize(objective)))


def _generate_name(objective: str) -> str:
    words = [w for w in objective.split() if len(w) > 2][:5]
    return " ".join(words).title() if words else objective[:40]


def _generate_description(objective: str, tool_sequence: list) -> str:
    tools   = [s["tool_name"] for s in tool_sequence]
    snippet = objective[:80].rstrip()
    tstr    = ", ".join(tools[:3]) + ("…" if len(tools) > 3 else "")
    return f"{snippet}. Via: {tstr}."


def _extract_tags(objective: str, tool_sequence: list) -> list:
    kw       = sorted(_tokenize(objective))
    tooltags = [s["tool_name"].replace("_", "-") for s in tool_sequence]
    # Intent categories inferred from objective keywords
    all_words = _tokenize(objective)
    intents   = sorted({_INTENT_MAP[w] for w in all_words if w in _INTENT_MAP})
    combined  = list(dict.fromkeys(kw + tooltags + intents))   # preserve order, dedup
    return combined[:10]   # cap raised to 10 to accommodate intent tags


# ── Applicability generators (Issue 4 — "when to use" signal) ────────────────

_TOOL_PREREQS: dict = {
    "email": "email credentials configured",
    "smtp":  "email credentials configured",
    "db":    "database connection available",
    "sql":   "database connection available",
    "postgres": "database connection available",
    "auth":  "authentication tokens valid",
    "oauth": "authentication tokens valid",
    "file":  "storage access granted",
    "blob":  "storage access granted",
    "api":   "network connectivity",
    "http":  "network connectivity",
    "request": "network connectivity",
}


def _generate_when_to_use(objective: str, tool_sequence: list) -> str:
    """Rule-based signal for when this skill applies (LLM-upgradeable in Phase 8C)."""
    verbs = [w for w in objective.lower().split() if w not in _STOPWORDS and len(w) > 3][:3]
    verb_str  = " and ".join(verbs) if verbs else "perform this operation"
    tool_list = ", ".join(s["tool_name"] for s in tool_sequence[:2])
    return f"Use when you need to {verb_str}. Requires tool(s): {tool_list}."


def _generate_pitfalls(tool_sequence: list) -> str:
    """Known risk areas inferred from tool names."""
    hints: list = []
    names = [s["tool_name"].lower() for s in tool_sequence]
    if any(k in n for n in names for k in ("email", "send", "notify", "sms")):
        hints.append("Avoid duplicate sends — verify idempotency_key before retry")
    if any(k in n for n in names for k in ("delete", "remove", "purge", "drop")):
        hints.append("Destructive operation — confirm target before execution")
    if any(k in n for n in names for k in ("auth", "token", "oauth", "login")):
        hints.append("Credentials may expire — handle auth errors with retry backoff")
    if any(k in n for n in names for k in ("file", "blob", "storage", "upload")):
        hints.append("Check storage quotas and permissions before writing")
    return "; ".join(hints) if hints else "No specific pitfalls recorded for this tool set"


def _generate_prerequisites(tool_sequence: list) -> list:
    """Inferred environment prerequisites from tool names."""
    prereqs: set = set()
    for step in tool_sequence:
        t = step["tool_name"].lower()
        for keyword, prereq in _TOOL_PREREQS.items():
            if keyword in t:
                prereqs.add(prereq)
    return sorted(prereqs) or ["no specific prerequisites identified"]


# ── Risk level inference (Issue 2) ────────────────────────────────────────────

def _infer_risk_level(objective: str, tool_sequence: list) -> str:
    """
    Classify risk by scanning tool names and objective for trigger keywords.
    Priority: HIGH > MEDIUM > LOW (first rule that matches wins).
    """
    # Build a flat string of all relevant text
    haystack = " ".join([
        objective.lower(),
        " ".join(s["tool_name"].lower() for s in tool_sequence),
    ])
    for keywords, level in _RISK_RULES:
        if any(kw in haystack for kw in keywords):
            return level
    return "LOW"


def _compute_confidence(success: int, failure: int) -> float:
    total = success + failure
    return round(success / total, 4) if total > 0 else 1.0


def _sanitize_args(args_hash: str) -> dict:
    """Never store raw arg values — expose only the opaque hash."""
    return {"args_hash": args_hash}


# ---------------------------------------------------------------------------
# Signature helpers (Issue 1 fix)
# ---------------------------------------------------------------------------

def _family_id(objective: str) -> str:
    """Hash of normalized objective only — groups all versions of same skill."""
    return hashlib.sha256(_normalize_objective(objective).encode()).hexdigest()


def _skill_id(tool_sequence: list, objective: str) -> str:
    """
    Compound hash: sorted tool names + normalized objective keywords.
    Different tool shapes OR different objective → different id.
    """
    tool_key = json.dumps(
        sorted(s["tool_name"] for s in tool_sequence)
    )
    return hashlib.sha256(
        f"{tool_key}|{_normalize_objective(objective)}".encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# DB fetch helpers
# ---------------------------------------------------------------------------

def _fetch_task(conn, task_id: int):
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def _fetch_tool_executions(conn, run_id: str) -> list:
    try:
        return conn.execute(
            """SELECT tool_name, args_hash, status, created_at
               FROM   tool_executions
               WHERE  idempotency_key LIKE ?
               ORDER  BY created_at ASC""",
            (f"{run_id}:%",),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------

def _qualifies(task, tool_rows: list) -> tuple:
    if task["status"] != "COMPLETED":
        return False, f"status={task['status']}"
    if (task["retry_count"] or 0) > 0:
        return False, f"retry_count={task['retry_count']} (dirty run)"
    for row in tool_rows:
        if row["status"] != "COMPLETED":
            return False, f"tool '{row['tool_name']}' status={row['status']}"
    return True, ""


# ---------------------------------------------------------------------------
# Core capture (Issues 1, 2, 3)
# ---------------------------------------------------------------------------

def create_skill_from_task(task_id: int):
    """
    Observe a completed task and persist a reusable skill record.

    Versioning logic:
      - Same family_id, same id  → increment success_count (dedup)
      - Same family_id, diff id  → new version (tool_sequence evolved)
      - New family_id            → version 1 of a new skill family

    Returns skill id on create/update, None when skipped.
    Logs SKILL_CREATED or SKILL_UPDATED.
    Never raises.
    """
    if task_id < 0:
        return None

    try:
        conn = _get_conn()
    except Exception as e:
        print(f"[SkillStore] DB connection failed: {e}")
        return None

    try:
        task = _fetch_task(conn, task_id)
        if task is None:
            return None

        run_id    = task["run_id"] or ""
        tool_rows = _fetch_tool_executions(conn, run_id) if run_id else []

        ok, reason = _qualifies(task, tool_rows)
        if not ok:
            return None     # silent skip

        # ── Extract objective ─────────────────────────────────────────────
        try:
            raw       = json.loads(task["input"] or "{}")
            objective = raw.get("input") or raw.get("source") or str(raw)
        except (json.JSONDecodeError, TypeError):
            objective = str(task["input"])

        # ── Build tool_sequence ───────────────────────────────────────────
        tool_sequence = [
            {"tool_name": r["tool_name"], "args_schema": _sanitize_args(r["args_hash"])}
            for r in tool_rows
        ] or [
            {
                "tool_name": "swarm_baton",
                "args_schema": {"objective_hash": hashlib.sha256(objective.encode()).hexdigest()},
            }
        ]

        # ── Issue 3 fix: complexity gate ──────────────────────────────────
        # Fallback baton tasks count as 1 step — skip those too.
        real_steps = len([s for s in tool_sequence if s["tool_name"] != "swarm_baton"])
        if real_steps < MIN_TOOL_STEPS and tool_sequence[0]["tool_name"] == "swarm_baton":
            # swarm_baton fallback → treat as 0 real steps, skip
            return None
        if real_steps > 0 and real_steps < MIN_TOOL_STEPS:
            # Real tools present but fewer than minimum → skip
            return None

        fam_id  = _family_id(objective)
        sk_id   = _skill_id(tool_sequence, objective)
        now     = datetime.now(timezone.utc).isoformat()

        # ── Case A: exact match (same id) → increment ──────────────────
        existing = conn.execute(
            "SELECT id, name, version, success_count, failure_count FROM skills WHERE id = ?",
            (sk_id,)
        ).fetchone()

        if existing:
            new_success = existing["success_count"] + 1
            new_conf    = _compute_confidence(new_success, existing["failure_count"])
            conn.execute(
                """UPDATE skills
                   SET success_count    = ?,
                       confidence_score = ?,
                       updated_at       = ?
                   WHERE id = ?""",
                (new_success, new_conf, now, sk_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO skill_sources (skill_id, task_id, version, captured_at) VALUES (?, ?, ?, ?)",
                (sk_id, task_id, existing["version"], now),
            )
            conn.commit()
            print(
                f"[SkillStore] SKILL_UPDATED  id={sk_id[:12]}…  "
                f"v{existing['version']}  name='{existing['name']}'  "
                f"success={new_success}  confidence={new_conf:.2f}"
            )
            return sk_id

        # ── Case B: same family, different id → new version (tool evolved) ─
        max_v_row = conn.execute(
            "SELECT MAX(version) AS mv FROM skills WHERE family_id = ?", (fam_id,)
        ).fetchone()
        version = (max_v_row["mv"] or 0) + 1

        # ── Case C: new family → version 1 ─────────────────────────────
        name         = _generate_name(objective)
        description  = _generate_description(objective, tool_sequence)
        tags         = json.dumps(_extract_tags(objective, tool_sequence))
        when_to_use  = _generate_when_to_use(objective, tool_sequence)
        pitfalls     = _generate_pitfalls(tool_sequence)
        prerequisites = json.dumps(_generate_prerequisites(tool_sequence))
        risk_level   = _infer_risk_level(objective, tool_sequence)

        conn.execute(
            """INSERT INTO skills
                   (id, family_id, version, name, description, input_pattern, tags,
                    tool_sequence, when_to_use, pitfalls, prerequisites, risk_level,
                    success_count, failure_count, confidence_score,
                    created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 1.0, ?, ?)""",
            (sk_id, fam_id, version, name, description, objective, tags,
             json.dumps(tool_sequence), when_to_use, pitfalls, prerequisites, risk_level,
             now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO skill_sources (skill_id, task_id, version, captured_at) VALUES (?, ?, ?, ?)",
            (sk_id, task_id, version, now),
        )
        conn.commit()
        print(
            f"[SkillStore] SKILL_CREATED  id={sk_id[:12]}…  "
            f"v{version}  name='{name}'  "
            f"risk={risk_level}  "
            f"intents={[t for t in json.loads(tags) if '-' in t or t in _INTENT_MAP.values()][:3]}"
        )
        return sk_id

    except Exception as e:
        print(f"[SkillStore] create_skill_from_task({task_id}) error: {e}")
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query API — token-overlap search
# (Semantic/hybrid upgrade deferred to Phase 8C)
# ---------------------------------------------------------------------------

def _overlap_score(query_tokens: set, skill: dict) -> float:
    """
    Two-tier weighted scorer with synonym expansion and bidirectional matching.

    Tier 1 ─ Primary gate (input_pattern, weight=_IP_WEIGHT):
      - Query tokens are EXPANDED with synonyms before matching (Issue 4).
      - Matching is BIDIRECTIONAL: skill tokens are also checked against
        expanded query, so a short query can still match a verbose skill.
      - Score = geometric mean of coverage and precision.
      - If expanded query has zero overlap with input_pattern → return 0.0 (hard gate).

    Tier 2 ─ Secondary boost (name + description only, max _SEC_WEIGHT):
      - Tags, when_to_use, prerequisites intentionally EXCLUDED.

    Final score multiplied by confidence_score so proven skills rank higher.
    """
    if not query_tokens:
        return 0.0

    # Issue 4: expand query tokens with synonyms for recall improvement
    expanded_query = _expand_tokens(query_tokens)

    # ── Tier 1: input_pattern gate (bidirectional) ──────────────────────────────
    ip_tokens = _tokenize(skill.get("input_pattern") or "")

    # Forward: expanded query tokens found in skill's input_pattern
    fwd_hits = len(expanded_query & ip_tokens)
    # Backward: skill's input_pattern tokens found in expanded query
    # (catches verbose skills matched by short queries)
    bwd_hits = len(ip_tokens & expanded_query)
    ip_hits  = max(fwd_hits, bwd_hits)   # take the more generous direction

    if ip_hits == 0:
        return 0.0    # hard gate: must have at least one token overlap (post-expansion)

    # Coverage: fraction of ORIGINAL (non-expanded) query that appears in input_pattern
    # Use original tokens for coverage to avoid inflating scores from spurious synonyms
    orig_hits = len(query_tokens & ip_tokens)  # un-expanded for precision
    coverage  = max(orig_hits, ip_hits) / len(query_tokens) if query_tokens else 0
    precision = ip_hits / max(len(ip_tokens), 1)
    ip_score  = (coverage * precision) ** 0.5

    # ── Tier 2: secondary name/description boost (capped) ──────────────────────
    sec_corpus = " ".join(filter(None, [
        skill.get("name", ""),
        skill.get("description", ""),
    ]))
    sec_tokens = _tokenize(sec_corpus)
    # Use expanded query for secondary boost too — improves recall on name matches
    sec_hits   = len(expanded_query & sec_tokens)
    sec_score  = min((sec_hits / len(query_tokens)) * _SEC_WEIGHT, _SEC_WEIGHT)

    base = (ip_score * _IP_WEIGHT) + sec_score
    return round(base * skill.get("confidence_score", 1.0), 4)


def list_skills(limit: int = 50) -> list:
    """Skills ordered by (confidence × success_count) descending."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT id, family_id, version, name, description, tags,
                      success_count, failure_count, confidence_score,
                      created_at, updated_at
               FROM   skills
               ORDER  BY (confidence_score * success_count) DESC
               LIMIT  ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[SkillStore] list_skills() error: {e}")
        return []


def search_skills(query: str, limit: int = 20, min_confidence: float = 0.0,
                  include_stale: bool = False) -> list:
    """
    Token-overlap search with synonym expansion and bidirectional matching.

    Parameters
    ----------
    query          : natural-language search string
    limit          : maximum results to return
    min_confidence : filter skills below this threshold (0.0 = no filter)
    include_stale  : if False (default), skip skills marked stale (Gap 3)

    Returns results sorted by combined overlap + confidence score.
    Falls back to list_skills() when query is empty.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return list_skills(limit)

    try:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM skills").fetchall()
        conn.close()
    except Exception as e:
        print(f"[SkillStore] search_skills() error: {e}")
        return []

    scored = []
    for row in rows:
        d = dict(row)
        if not include_stale and d.get("is_stale", 0):
            continue    # Gap 3: skip stale skills unless explicitly requested
        if d.get("confidence_score", 1.0) < min_confidence:
            continue    # confidence gate
        score = _overlap_score(query_tokens, d)
        if score > 0:
            scored.append((score, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:limit]]


def recommend_skill(query: str, min_confidence: float = CONFIDENCE_GATE,
                    include_stale: bool = False, resolve_ambiguity_fn=None):
    """
    Return the single best skill for a query, enforcing the confidence gate.

    Stale skills (is_stale=1) are excluded by default (Gap 3).

    Phase 9 Gap 3 (Ambiguity Resolution):
    If the top two skills have scores within 5% of each other, and
    resolve_ambiguity_fn is provided, it is called to pick the winner.
    Signature: resolve_ambiguity_fn(query, list_of_ambiguous_skills) -> best_skill | None

    Returns None if:
      - no skill matches the query
      - the best match is below min_confidence
      - the best match is stale and include_stale=False

    Caller should inspect result["risk_level"] before auto-executing:
      - 'HIGH'   → require explicit user confirmation
      - 'MEDIUM' → surface a warning
      - 'LOW'    → safe for automatic use
    """
    results = search_skills(query, limit=5, min_confidence=min_confidence,
                            include_stale=include_stale)
    if not results:
        return None

    if len(results) > 1 and resolve_ambiguity_fn is not None:
        # Re-score to get exact values (search_skills currently strips scores)
        # We know they match, so we just calculate _overlap_score again.
        query_tokens = _tokenize(query)
        scored = [(s, _overlap_score(query_tokens, s)) for s in results]
        
        best_score = scored[0][1]
        second_score = scored[1][1]

        # Ambiguity threshold: 5% difference
        if best_score > 0 and (best_score - second_score) / best_score < 0.05:
            # Collect all skills within the ambiguity window
            ambiguous = [s for s, sc in scored if (best_score - sc) / best_score < 0.05]
            resolved = resolve_ambiguity_fn(query, ambiguous)
            if resolved:
                return resolved

    return results[0]


def get_skill(skill_id: str):
    """Fetch a single skill by id, including its source task ids."""
    try:
        conn  = _get_conn()
        row   = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if row is None:
            conn.close()
            return None
        result = dict(row)
        sources = conn.execute(
            "SELECT task_id, version, captured_at FROM skill_sources WHERE skill_id = ? ORDER BY captured_at",
            (skill_id,)
        ).fetchall()
        result["source_tasks"] = [dict(s) for s in sources]
        conn.close()
        return result
    except Exception as e:
        print(f"[SkillStore] get_skill() error: {e}")
        return None


def get_skill_sources(skill_id: str) -> list:
    """All originating task_ids for a skill — for debugging / audit / rollback."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT task_id, version, captured_at FROM skill_sources WHERE skill_id = ? ORDER BY captured_at",
            (skill_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[SkillStore] get_skill_sources() error: {e}")
        return []
