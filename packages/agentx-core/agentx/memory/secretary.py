import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / ".agentx" / "aja_secretary.sqlite3"

TASK_STATUSES = {"pending", "active", "blocked", "completed", "archived"}
APPROVAL_STATUSES = {"not_required", "pending", "approved", "rejected"}
PRIORITY_VALUES = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
COMMUNICATION_CHANNELS = {"telegram", "email", "draft", "other"}
DELIVERY_STATUSES = {"draft", "ready", "sent", "failed", "cancelled"}
REVIEW_KINDS = {"morning", "night", "weekly"}


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def normalize_priority(value: Any) -> str:
    if value is None:
        return "medium"
    text = str(value).strip().lower()
    return text if text in PRIORITY_VALUES else "medium"


def json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=True)


def json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


class SecretaryMemory:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS secretary_tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    context TEXT,
                    owner TEXT,
                    due_date TEXT,
                    recurrence TEXT,
                    priority TEXT NOT NULL DEFAULT 'medium',
                    status TEXT NOT NULL DEFAULT 'pending',
                    follow_up_state TEXT NOT NULL DEFAULT '{}',
                    reminder_state TEXT NOT NULL DEFAULT '{}',
                    escalation_level INTEGER NOT NULL DEFAULT 0,
                    approval_required INTEGER NOT NULL DEFAULT 0,
                    approval_status TEXT NOT NULL DEFAULT 'not_required',
                    related_people TEXT NOT NULL DEFAULT '[]',
                    communication_history TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'system',
                    last_reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_secretary_tasks_status ON secretary_tasks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_secretary_tasks_due_date ON secretary_tasks(due_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_secretary_tasks_priority ON secretary_tasks(priority)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS secretary_communications (
                    message_id TEXT PRIMARY KEY,
                    recipient TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    subject TEXT,
                    draft_content TEXT NOT NULL,
                    tone_profile TEXT NOT NULL DEFAULT 'professional',
                    approval_required INTEGER NOT NULL DEFAULT 1,
                    approval_status TEXT NOT NULL DEFAULT 'pending',
                    follow_up_required INTEGER NOT NULL DEFAULT 0,
                    follow_up_due TEXT,
                    related_task_id TEXT,
                    communication_history TEXT NOT NULL DEFAULT '[]',
                    delivery_status TEXT NOT NULL DEFAULT 'draft',
                    last_sent_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (related_task_id) REFERENCES secretary_tasks(task_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_secretary_communications_status ON secretary_communications(delivery_status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_secretary_communications_followup ON secretary_communications(follow_up_required, follow_up_due)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduler_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduler_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    target_id TEXT,
                    payload TEXT NOT NULL DEFAULT '{}',
                    delivered_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduler_events_type_created ON scheduler_events(event_type, created_at)")
            # --- AJA Brain: canonical approval store ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS aja_approvals (
                    approval_id TEXT PRIMARY KEY,
                    tool TEXT NOT NULL DEFAULT 'bash',
                    command TEXT,
                    command_preview TEXT,
                    action_type TEXT,
                    root_binary TEXT,
                    risk_level TEXT NOT NULL DEFAULT 'medium',
                    level TEXT,
                    reasons TEXT NOT NULL DEFAULT '[]',
                    human_reason TEXT,
                    rollback_path TEXT,
                    dry_run_summary TEXT,
                    requester_source TEXT NOT NULL DEFAULT 'CLI',
                    telegram_meta TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    expires_at TEXT,
                    resolved_at TEXT,
                    resolution_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_aja_approvals_status ON aja_approvals(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_aja_approvals_created ON aja_approvals(created_at)")
            # Append-only audit trail for every approval lifecycle transition
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS aja_approval_audit (
                    audit_id TEXT PRIMARY KEY,
                    approval_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'system',
                    requester_source TEXT,
                    command TEXT,
                    risk_level TEXT,
                    reasons TEXT,
                    exit_code INTEGER,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (approval_id) REFERENCES aja_approvals(approval_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_aja_approval_audit_id ON aja_approval_audit(approval_id)")
            # Runtime event feed (rolling window, capped at 500 rows)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS aja_runtime_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    tool TEXT,
                    message TEXT NOT NULL DEFAULT '',
                    command TEXT,
                    root_binary TEXT,
                    level TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_aja_runtime_events_created ON aja_runtime_events(created_at DESC)")
            # --- Phase 6.1: Worker Capability Registry ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_registry (
                    worker_id TEXT PRIMARY KEY,
                    worker_name TEXT NOT NULL,
                    worker_type TEXT NOT NULL DEFAULT 'cli_agent',
                    availability_status TEXT NOT NULL DEFAULT 'available',
                    primary_strengths TEXT NOT NULL DEFAULT '[]',
                    weak_areas TEXT NOT NULL DEFAULT '[]',
                    preferred_task_types TEXT NOT NULL DEFAULT '[]',
                    blocked_task_types TEXT NOT NULL DEFAULT '[]',
                    execution_speed TEXT NOT NULL DEFAULT 'medium',
                    reliability_score REAL NOT NULL DEFAULT 0.8,
                    cost_profile TEXT NOT NULL DEFAULT 'subscription',
                    approval_risk_level TEXT NOT NULL DEFAULT 'medium',
                    supports_tests INTEGER NOT NULL DEFAULT 0,
                    supports_git_operations INTEGER NOT NULL DEFAULT 0,
                    supports_deployment INTEGER NOT NULL DEFAULT 0,
                    supports_plan_mode INTEGER NOT NULL DEFAULT 0,
                    requires_manual_review INTEGER NOT NULL DEFAULT 1,
                    historical_success_rate REAL NOT NULL DEFAULT 0.0,
                    total_tasks_executed INTEGER NOT NULL DEFAULT 0,
                    total_tasks_failed INTEGER NOT NULL DEFAULT 0,
                    recommended_use_cases TEXT NOT NULL DEFAULT '[]',
                    known_failure_patterns TEXT NOT NULL DEFAULT '[]',
                    recent_failures TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    last_reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_worker_registry_status ON worker_registry(availability_status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_worker_registry_type ON worker_registry(worker_type)")
            # Worker execution history for trend tracking
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_execution_log (
                    log_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    task_type TEXT,
                    task_description TEXT,
                    outcome TEXT NOT NULL DEFAULT 'unknown',
                    duration_seconds INTEGER,
                    error_summary TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (worker_id) REFERENCES worker_registry(worker_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_worker_exec_log_worker ON worker_execution_log(worker_id, created_at DESC)")

    # ─── Worker Registry Methods ──────────────────────────────────────────────

    def create_worker(self, data: dict[str, Any]) -> dict[str, Any]:
        """Register a new worker in the capability registry."""
        now = utc_now()
        worker = {
            "worker_id": data.get("worker_id") or f"worker-{uuid.uuid4().hex[:12]}",
            "worker_name": str(data.get("worker_name") or "").strip(),
            "worker_type": str(data.get("worker_type") or "cli_agent").strip(),
            "availability_status": str(data.get("availability_status") or "available").strip(),
            "primary_strengths": data.get("primary_strengths") or [],
            "weak_areas": data.get("weak_areas") or [],
            "preferred_task_types": data.get("preferred_task_types") or [],
            "blocked_task_types": data.get("blocked_task_types") or [],
            "execution_speed": str(data.get("execution_speed") or "medium").strip(),
            "reliability_score": float(data.get("reliability_score", 0.8)),
            "cost_profile": str(data.get("cost_profile") or "subscription").strip(),
            "approval_risk_level": str(data.get("approval_risk_level") or "medium").strip(),
            "supports_tests": bool(data.get("supports_tests", False)),
            "supports_git_operations": bool(data.get("supports_git_operations", False)),
            "supports_deployment": bool(data.get("supports_deployment", False)),
            "supports_plan_mode": bool(data.get("supports_plan_mode", False)),
            "requires_manual_review": bool(data.get("requires_manual_review", True)),
            "historical_success_rate": float(data.get("historical_success_rate", 0.0)),
            "total_tasks_executed": int(data.get("total_tasks_executed", 0)),
            "total_tasks_failed": int(data.get("total_tasks_failed", 0)),
            "recommended_use_cases": data.get("recommended_use_cases") or [],
            "known_failure_patterns": data.get("known_failure_patterns") or [],
            "recent_failures": data.get("recent_failures") or [],
            "metadata": data.get("metadata") or {},
            "last_reviewed_at": data.get("last_reviewed_at"),
            "created_at": data.get("created_at") or now,
            "updated_at": now,
        }
        if not worker["worker_name"]:
            raise ValueError("Worker name is required.")

        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO worker_registry (
                    worker_id, worker_name, worker_type, availability_status,
                    primary_strengths, weak_areas, preferred_task_types, blocked_task_types,
                    execution_speed, reliability_score, cost_profile, approval_risk_level,
                    supports_tests, supports_git_operations, supports_deployment, supports_plan_mode,
                    requires_manual_review, historical_success_rate, total_tasks_executed, total_tasks_failed,
                    recommended_use_cases, known_failure_patterns, recent_failures, metadata,
                    last_reviewed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worker["worker_id"], worker["worker_name"], worker["worker_type"],
                    worker["availability_status"],
                    json_dump(worker["primary_strengths"]), json_dump(worker["weak_areas"]),
                    json_dump(worker["preferred_task_types"]), json_dump(worker["blocked_task_types"]),
                    worker["execution_speed"], worker["reliability_score"],
                    worker["cost_profile"], worker["approval_risk_level"],
                    1 if worker["supports_tests"] else 0,
                    1 if worker["supports_git_operations"] else 0,
                    1 if worker["supports_deployment"] else 0,
                    1 if worker["supports_plan_mode"] else 0,
                    1 if worker["requires_manual_review"] else 0,
                    worker["historical_success_rate"],
                    worker["total_tasks_executed"], worker["total_tasks_failed"],
                    json_dump(worker["recommended_use_cases"]),
                    json_dump(worker["known_failure_patterns"]),
                    json_dump(worker["recent_failures"]),
                    json_dump(worker["metadata"]),
                    worker["last_reviewed_at"], worker["created_at"], worker["updated_at"],
                ),
            )
        return worker

    def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM worker_registry WHERE worker_id = ?", (worker_id,)).fetchone()
        return _row_to_worker(row) if row else None

    def list_workers(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("availability_status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM worker_registry
            {where}
            ORDER BY reliability_score DESC, worker_name ASC
            LIMIT ?
        """
        params.append(max(1, min(int(limit), 100)))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_worker(row) for row in rows]

    def update_worker(self, worker_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_worker(worker_id)
        if not existing:
            raise KeyError(f"Worker not found: {worker_id}")

        allowed = {
            "worker_name", "worker_type", "availability_status",
            "primary_strengths", "weak_areas", "preferred_task_types", "blocked_task_types",
            "execution_speed", "reliability_score", "cost_profile", "approval_risk_level",
            "supports_tests", "supports_git_operations", "supports_deployment", "supports_plan_mode",
            "requires_manual_review", "historical_success_rate",
            "total_tasks_executed", "total_tasks_failed",
            "recommended_use_cases", "known_failure_patterns", "recent_failures",
            "metadata", "last_reviewed_at",
        }
        changed: dict[str, Any] = {}
        for key, value in updates.items():
            if key not in allowed:
                continue
            changed[key] = value

        if not changed:
            return existing
        changed["updated_at"] = utc_now()

        assignments = []
        params = []
        for key, value in changed.items():
            assignments.append(f"{key} = ?")
            if key in {"primary_strengths", "weak_areas", "preferred_task_types", "blocked_task_types",
                        "recommended_use_cases", "known_failure_patterns", "recent_failures", "metadata"}:
                value = json_dump(value)
            elif key in {"supports_tests", "supports_git_operations", "supports_deployment",
                         "supports_plan_mode", "requires_manual_review"}:
                value = 1 if value else 0
            params.append(value)
        params.append(worker_id)

        with self.connect() as conn:
            conn.execute(f"UPDATE worker_registry SET {', '.join(assignments)} WHERE worker_id = ?", params)
        return self.get_worker(worker_id) or existing

    def delete_worker(self, worker_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM worker_registry WHERE worker_id = ?", (worker_id,))
            return cursor.rowcount > 0

    def log_worker_execution(self, data: dict[str, Any]) -> dict[str, Any]:
        """Log a worker execution outcome and update aggregate stats."""
        now = utc_now()
        log_id = f"wlog-{uuid.uuid4().hex[:12]}"
        worker_id = data["worker_id"]
        outcome = data.get("outcome", "unknown")

        with self.connect() as conn:
            conn.execute(
                "INSERT INTO worker_execution_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (log_id, worker_id, data.get("task_type"), data.get("task_description"),
                 outcome, data.get("duration_seconds"), data.get("error_summary"),
                 data.get("notes"), now),
            )
            # Update aggregate stats inline (single connection to avoid locking)
            row = conn.execute("SELECT * FROM worker_registry WHERE worker_id = ?", (worker_id,)).fetchone()
            if row:
                worker = _row_to_worker(row)
                total_exec = worker["total_tasks_executed"] + 1
                total_fail = worker["total_tasks_failed"] + (1 if outcome == "failure" else 0)
                success_rate = ((total_exec - total_fail) / total_exec) * 100 if total_exec > 0 else 0
                recent = worker["recent_failures"]
                if outcome == "failure":
                    recent = ([{
                        "task_type": data.get("task_type"),
                        "error": data.get("error_summary", ""),
                        "at": now,
                    }] + recent)[:10]
                conn.execute(
                    """UPDATE worker_registry SET
                        total_tasks_executed = ?,
                        total_tasks_failed = ?,
                        historical_success_rate = ?,
                        recent_failures = ?,
                        last_reviewed_at = ?,
                        updated_at = ?
                    WHERE worker_id = ?""",
                    (total_exec, total_fail, round(success_rate, 1),
                     json_dump(recent), now, now, worker_id),
                )
        return {"log_id": log_id, "worker_id": worker_id, "outcome": outcome, "created_at": now}

    def get_worker_execution_history(self, worker_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM worker_execution_log WHERE worker_id = ? ORDER BY created_at DESC LIMIT ?",
                (worker_id, min(limit, 100)),
            ).fetchall()
        return [dict(r) for r in rows]

    def seed_default_workers(self) -> list[dict[str, Any]]:
        """Seed the registry with known worker profiles if they don't already exist."""
        defaults = _default_worker_profiles()
        seeded = []
        for profile in defaults:
            existing = self.get_worker(profile["worker_id"])
            if not existing:
                worker = self.create_worker(profile)
                seeded.append(worker)
        return seeded

    def create_approval(self, data: dict[str, Any]) -> str:
        aid = data.get("approval_id") or uuid.uuid4().hex
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO aja_approvals (
                    approval_id, tool, command, command_preview, action_type, root_binary,
                    risk_level, level, reasons, human_reason, rollback_path, dry_run_summary,
                    requester_source, telegram_meta, status, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (aid, data.get("tool", "bash"), data.get("command"), data.get("command_preview"),
                 data.get("action_type"), data.get("root_binary"), data.get("risk_level", "medium"),
                 data.get("level"), json_dump(data.get("reasons")), data.get("human_reason"),
                 data.get("rollback_path"), data.get("dry_run_summary"), data.get("requester_source", "CLI"),
                 json_dump(data.get("telegram_meta")), "pending", data.get("expires_at"), now, now)
            )
        return aid

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM aja_approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            return dict(row) if row else None

    def update_approval(self, approval_id: str, status: str, note: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE aja_approvals SET status = ?, resolution_note = ?, resolved_at = ?, updated_at = ? WHERE approval_id = ?",
                (status, note, utc_now(), utc_now(), approval_id)
            )

    def log_approval_audit(self, data: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO aja_approval_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, data["approval_id"], data["action"], data.get("actor", "system"),
                 data.get("requester_source"), data.get("command"), data.get("risk_level"),
                 json_dump(data.get("reasons")), data.get("exit_code"), data.get("note"), utc_now())
            )

    def add_runtime_event(self, data: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO aja_runtime_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, data["event_type"], data.get("tool"), data.get("message", ""),
                 data.get("command"), data.get("root_binary"), data.get("level"),
                 json_dump(data.get("metadata")), utc_now())
            )
            # Maintain rolling window of 500
            conn.execute(
                "DELETE FROM aja_runtime_events WHERE event_id IN (SELECT event_id FROM aja_runtime_events ORDER BY created_at DESC LIMIT -1 OFFSET 500)"
            )

    def get_runtime_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM aja_runtime_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_active_approval(self) -> dict[str, Any] | None:
        """Return the single most-recent pending (non-resolved) approval, or None."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM aja_approvals WHERE status = 'pending' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["reasons"] = json_load(d.get("reasons"), [])
            d["telegram_meta"] = json_load(d.get("telegram_meta"), {})
            return d

    def list_approval_audit(self, approval_id: str) -> list[dict[str, Any]]:
        """Return the full audit trail for a given approval_id, oldest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM aja_approval_audit WHERE approval_id = ? ORDER BY created_at ASC",
                (approval_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        status = str(data.get("status") or "pending").lower()
        if status not in TASK_STATUSES:
            status = "pending"
        approval_required = bool(data.get("approval_required", False))
        approval_status = str(data.get("approval_status") or ("pending" if approval_required else "not_required")).lower()
        if approval_status not in APPROVAL_STATUSES:
            approval_status = "pending" if approval_required else "not_required"

        task = {
            "task_id": data.get("task_id") or f"task-{uuid.uuid4().hex[:12]}",
            "title": str(data.get("title") or "").strip(),
            "context": str(data.get("context") or "").strip(),
            "owner": str(data.get("owner") or "AJA").strip(),
            "due_date": normalize_due_date(data.get("due_date")),
            "recurrence": normalize_recurrence(data.get("recurrence")),
            "priority": normalize_priority(data.get("priority")),
            "status": status,
            "follow_up_state": data.get("follow_up_state") or {"state": "not_started"},
            "reminder_state": data.get("reminder_state") or {"enabled": bool(data.get("due_date")), "last_sent_at": None},
            "escalation_level": int(data.get("escalation_level") or 0),
            "approval_required": approval_required,
            "approval_status": approval_status,
            "related_people": data.get("related_people") or [],
            "communication_history": data.get("communication_history") or [],
            "source": str(data.get("source") or "system"),
            "last_reviewed_at": data.get("last_reviewed_at"),
            "created_at": data.get("created_at") or now,
            "updated_at": now,
        }
        if not task["title"]:
            raise ValueError("Task title is required.")

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO secretary_tasks (
                    task_id, title, context, owner, due_date, recurrence, priority, status,
                    follow_up_state, reminder_state, escalation_level, approval_required,
                    approval_status, related_people, communication_history, source,
                    last_reviewed_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["task_id"],
                    task["title"],
                    task["context"],
                    task["owner"],
                    task["due_date"],
                    task["recurrence"],
                    task["priority"],
                    task["status"],
                    json_dump(task["follow_up_state"]),
                    json_dump(task["reminder_state"]),
                    task["escalation_level"],
                    1 if task["approval_required"] else 0,
                    task["approval_status"],
                    json_dump(task["related_people"]),
                    json_dump(task["communication_history"]),
                    task["source"],
                    task["last_reviewed_at"],
                    task["created_at"],
                    task["updated_at"],
                ),
            )
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM secretary_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_task(row) if row else None

    def list_tasks(
        self,
        statuses: list[str] | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        clauses = []
        if statuses:
            safe_statuses = [status for status in statuses if status in TASK_STATUSES]
            if safe_statuses:
                clauses.append(f"status IN ({','.join('?' for _ in safe_statuses)})")
                params.extend(safe_statuses)
        elif not include_archived:
            clauses.append("status != ?")
            params.append("archived")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM secretary_tasks
            {where}
            ORDER BY
                CASE priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
                due_date ASC,
                updated_at DESC
            LIMIT ?
        """
        params.append(max(1, min(int(limit), 200)))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_task(row) for row in rows]

    def update_task(self, task_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_task(task_id)
        if not existing:
            raise KeyError(f"Task not found: {task_id}")

        allowed = {
            "title",
            "context",
            "owner",
            "due_date",
            "recurrence",
            "priority",
            "status",
            "follow_up_state",
            "reminder_state",
            "escalation_level",
            "approval_required",
            "approval_status",
            "related_people",
            "communication_history",
            "source",
            "last_reviewed_at",
        }
        changed: dict[str, Any] = {}
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "due_date":
                value = normalize_due_date(value)
            elif key == "recurrence":
                value = normalize_recurrence(value)
            elif key == "priority":
                value = normalize_priority(value)
            elif key == "status":
                value = str(value).lower()
                if value not in TASK_STATUSES:
                    continue
            elif key == "approval_status":
                value = str(value).lower()
                if value not in APPROVAL_STATUSES:
                    continue
            changed[key] = value

        if not changed:
            return existing
        changed["updated_at"] = utc_now()

        assignments = []
        params = []
        for key, value in changed.items():
            assignments.append(f"{key} = ?")
            if key in {"follow_up_state", "reminder_state", "related_people", "communication_history"}:
                value = json_dump(value)
            elif key == "approval_required":
                value = 1 if value else 0
            params.append(value)
        params.append(task_id)

        with self.connect() as conn:
            conn.execute(f"UPDATE secretary_tasks SET {', '.join(assignments)} WHERE task_id = ?", params)
        return self.get_task(task_id) or existing

    def complete_task(self, task_id: str, note: str = "") -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task not found: {task_id}")

        history = task["communication_history"]
        history.append({"at": utc_now(), "type": "completion", "message": note or "Marked completed."})
        recurrence = parse_recurrence(task.get("recurrence"))
        if recurrence:
            next_due = next_recurrence_date(task.get("due_date"), recurrence)
            return self.update_task(
                task_id,
                {
                    "status": "pending",
                    "due_date": next_due,
                    "last_reviewed_at": utc_now(),
                    "escalation_level": 0,
                    "communication_history": history,
                    "follow_up_state": {"state": "next_occurrence_scheduled", "last_completed_at": utc_now()},
                },
            )

        return self.update_task(
            task_id,
            {
                "status": "completed",
                "last_reviewed_at": utc_now(),
                "communication_history": history,
                "follow_up_state": {"state": "completed"},
            },
        )

    def archive_task(self, task_id: str) -> dict[str, Any]:
        return self.update_task(task_id, {"status": "archived", "last_reviewed_at": utc_now()})

    def review(self, stale_after_days: int = 7, due_soon_hours: int = 24, escalate: bool = True) -> dict[str, Any]:
        now = datetime.utcnow()
        tasks = self.list_tasks(statuses=["pending", "active", "blocked"], include_archived=False, limit=200)
        overdue = []
        due_soon = []
        stale = []
        blocked = []

        for task in tasks:
            due = parse_iso(task.get("due_date"))
            reviewed = parse_iso(task.get("last_reviewed_at") or task.get("created_at"))
            is_overdue = bool(due and due < now)
            is_due_soon = bool(due and now <= due <= now + timedelta(hours=due_soon_hours))
            is_stale = bool(reviewed and reviewed < now - timedelta(days=stale_after_days))

            if task["status"] == "blocked":
                blocked.append(task)
            if is_overdue:
                overdue.append(task)
            elif is_due_soon:
                due_soon.append(task)
            if is_stale:
                stale.append(task)
                if escalate:
                    self.update_task(
                        task["task_id"],
                        {
                            "escalation_level": min(5, int(task.get("escalation_level") or 0) + 1),
                            "last_reviewed_at": utc_now(),
                            "follow_up_state": {
                                **(task.get("follow_up_state") or {}),
                                "state": "stale_review",
                                "last_stale_detected_at": utc_now(),
                            },
                        },
                    )

        return {
            "overdue": overdue,
            "due_soon": due_soon,
            "stale": stale,
            "blocked": blocked,
            "active_count": len(tasks),
            "reviewed_at": utc_now(),
        }

    def summary(self, limit: int = 10) -> str:
        review = self.review(escalate=False)
        tasks = self.list_tasks(statuses=["pending", "active", "blocked"], limit=limit)
        return format_tasks_for_mobile(tasks, review)

    def create_communication(self, data: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        channel = normalize_channel(data.get("channel"))
        approval_required = True
        approval_status = "pending"

        message = {
            "message_id": data.get("message_id") or f"msg-{uuid.uuid4().hex[:12]}",
            "recipient": str(data.get("recipient") or "").strip(),
            "channel": channel,
            "subject": str(data.get("subject") or "").strip(),
            "draft_content": str(data.get("draft_content") or "").strip(),
            "tone_profile": str(data.get("tone_profile") or "professional").strip().lower(),
            "approval_required": approval_required,
            "approval_status": approval_status,
            "follow_up_required": bool(data.get("follow_up_required", False)),
            "follow_up_due": normalize_due_date(data.get("follow_up_due")),
            "related_task_id": data.get("related_task_id"),
            "communication_history": data.get("communication_history")
            or [{"at": now, "type": "draft_created", "message": "Draft created. Approval required before sending."}],
            "delivery_status": normalize_delivery_status(data.get("delivery_status") or "draft"),
            "last_sent_at": data.get("last_sent_at"),
            "created_at": data.get("created_at") or now,
            "updated_at": now,
        }
        if not message["recipient"]:
            raise ValueError("Message recipient is required.")
        if not message["draft_content"]:
            raise ValueError("Draft content is required.")

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO secretary_communications (
                    message_id, recipient, channel, subject, draft_content, tone_profile,
                    approval_required, approval_status, follow_up_required, follow_up_due,
                    related_task_id, communication_history, delivery_status, last_sent_at,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["message_id"],
                    message["recipient"],
                    message["channel"],
                    message["subject"],
                    message["draft_content"],
                    message["tone_profile"],
                    1 if message["approval_required"] else 0,
                    message["approval_status"],
                    1 if message["follow_up_required"] else 0,
                    message["follow_up_due"],
                    message["related_task_id"],
                    json_dump(message["communication_history"]),
                    message["delivery_status"],
                    message["last_sent_at"],
                    message["created_at"],
                    message["updated_at"],
                ),
            )
        return message

    def get_communication(self, message_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM secretary_communications WHERE message_id = ?", (message_id,)).fetchone()
        return row_to_communication(row) if row else None

    def list_communications(
        self,
        delivery_status: str | None = None,
        approval_status: str | None = None,
        pending_follow_up: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if delivery_status:
            clauses.append("delivery_status = ?")
            params.append(normalize_delivery_status(delivery_status))
        if approval_status:
            status = str(approval_status).lower()
            if status in APPROVAL_STATUSES:
                clauses.append("approval_status = ?")
                params.append(status)
        if pending_follow_up:
            clauses.append("follow_up_required = 1")
            clauses.append("(delivery_status = 'sent' OR delivery_status = 'ready')")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM secretary_communications
            {where}
            ORDER BY
                CASE WHEN follow_up_due IS NULL THEN 1 ELSE 0 END,
                follow_up_due ASC,
                updated_at DESC
            LIMIT ?
        """
        params.append(max(1, min(int(limit), 200)))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_communication(row) for row in rows]

    def update_communication(self, message_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_communication(message_id)
        if not existing:
            raise KeyError(f"Message not found: {message_id}")

        allowed = {
            "recipient",
            "channel",
            "subject",
            "draft_content",
            "tone_profile",
            "approval_required",
            "approval_status",
            "follow_up_required",
            "follow_up_due",
            "related_task_id",
            "communication_history",
            "delivery_status",
            "last_sent_at",
        }
        changed: dict[str, Any] = {}
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "channel":
                value = normalize_channel(value)
            elif key == "delivery_status":
                value = normalize_delivery_status(value)
            elif key == "approval_status":
                value = str(value).lower()
                if value not in APPROVAL_STATUSES:
                    continue
            elif key == "approval_required":
                value = True
            elif key == "follow_up_due":
                value = normalize_due_date(value)
            changed[key] = value

        if not changed:
            return existing
        changed["updated_at"] = utc_now()
        assignments = []
        params = []
        for key, value in changed.items():
            assignments.append(f"{key} = ?")
            if key == "communication_history":
                value = json_dump(value)
            elif key in {"approval_required", "follow_up_required"}:
                value = 1 if value else 0
            params.append(value)
        params.append(message_id)

        with self.connect() as conn:
            conn.execute(f"UPDATE secretary_communications SET {', '.join(assignments)} WHERE message_id = ?", params)
        return self.get_communication(message_id) or existing

    def edit_communication(self, message_id: str, draft_content: str, note: str = "") -> dict[str, Any]:
        message = self.get_communication(message_id)
        if not message:
            raise KeyError(f"Message not found: {message_id}")
        history = message["communication_history"]
        history.append({"at": utc_now(), "type": "edited", "message": note or "Draft edited."})
        return self.update_communication(
            message_id,
            {
                "draft_content": draft_content,
                "approval_status": "pending",
                "delivery_status": "draft",
                "communication_history": history,
            },
        )

    def approve_communication(self, message_id: str) -> dict[str, Any]:
        message = self.get_communication(message_id)
        if not message:
            raise KeyError(f"Message not found: {message_id}")
        history = message["communication_history"]
        history.append({"at": utc_now(), "type": "approved", "message": "Draft approved for sending."})
        return self.update_communication(
            message_id,
            {"approval_status": "approved", "delivery_status": "ready", "communication_history": history},
        )

    def reject_communication(self, message_id: str, reason: str = "") -> dict[str, Any]:
        message = self.get_communication(message_id)
        if not message:
            raise KeyError(f"Message not found: {message_id}")
        history = message["communication_history"]
        history.append({"at": utc_now(), "type": "rejected", "message": reason or "Draft rejected."})
        return self.update_communication(
            message_id,
            {"approval_status": "rejected", "delivery_status": "cancelled", "communication_history": history},
        )

    def mark_communication_sent(self, message_id: str, delivery_note: str = "") -> dict[str, Any]:
        message = self.get_communication(message_id)
        if not message:
            raise KeyError(f"Message not found: {message_id}")
        if message["approval_status"] != "approved":
            raise PermissionError("Outbound messages require approval before sending.")

        now = utc_now()
        history = message["communication_history"]
        history.append({"at": now, "type": "sent", "message": delivery_note or "Marked sent."})
        updates: dict[str, Any] = {
            "delivery_status": "sent",
            "last_sent_at": now,
            "communication_history": history,
        }
        sent = self.update_communication(message_id, updates)
        if sent["follow_up_required"] and sent.get("follow_up_due"):
            self.create_follow_up_task(sent)
        return sent

    def create_follow_up_task(self, message: dict[str, Any]) -> dict[str, Any] | None:
        title = f"Follow up with {message['recipient']} about {message.get('subject') or 'message'}"
        existing_task_id = message.get("related_task_id")
        if existing_task_id and self.get_task(existing_task_id):
            return None
        task = self.create_task(
            {
                "title": title,
                "context": f"Follow-up for message {message['message_id']}: {message['draft_content']}",
                "owner": "AJA",
                "due_date": message.get("follow_up_due"),
                "priority": "medium",
                "status": "pending",
                "source": "system",
                "follow_up_state": {"state": "waiting_for_reply", "message_id": message["message_id"]},
                "related_people": [message["recipient"]],
                "communication_history": [{"at": utc_now(), "type": "linked_message", "message": message["message_id"]}],
            }
        )
        self.update_communication(message["message_id"], {"related_task_id": task["task_id"]})
        return task

    def communication_summary(self, limit: int = 10) -> str:
        drafts = self.list_communications(delivery_status="draft", limit=limit)
        ready = self.list_communications(delivery_status="ready", limit=limit)
        followups = self.list_communications(pending_follow_up=True, limit=limit)
        return format_communications_for_mobile(drafts, ready, followups)

    def get_scheduler_config(self) -> dict[str, Any]:
        config = default_scheduler_config()
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM scheduler_settings").fetchall()
        for row in rows:
            config[row["key"]] = json_load(row["value"], config.get(row["key"]))
        return config

    def update_scheduler_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = set(default_scheduler_config().keys())
        now = utc_now()
        with self.connect() as conn:
            for key, value in updates.items():
                if key not in allowed:
                    continue
                conn.execute(
                    """
                    INSERT INTO scheduler_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (key, json_dump(value), now),
                )
        return self.get_scheduler_config()

    def snooze_task(self, task_id: str, until: Any = "tomorrow", reason: str = "") -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task not found: {task_id}")
        until_iso = normalize_due_date(until) or normalize_due_date("tomorrow")
        reminder_state = task.get("reminder_state") or {}
        reminder_state.update({"snoozed_until": until_iso, "snooze_reason": reason, "snoozed_at": utc_now()})
        history = task["communication_history"]
        history.append({"at": utc_now(), "type": "snooze", "message": reason or f"Snoozed until {until_iso}."})
        return self.update_task(task_id, {"reminder_state": reminder_state, "communication_history": history})

    def urgency_score_task(self, task: dict[str, Any]) -> int:
        now = datetime.utcnow()
        score = PRIORITY_VALUES.get(task.get("priority"), 2) * 10
        due = parse_iso(task.get("due_date"))
        if due:
            if due < now:
                score += 50
            elif due <= now + timedelta(hours=24):
                score += 25
            elif due <= now + timedelta(days=7):
                score += 10
        score += int(task.get("escalation_level") or 0) * 12
        if task.get("status") == "blocked":
            score += 15
        if task.get("approval_required") and task.get("approval_status") == "pending":
            score += 8
        return score

    def generate_executive_review(self, kind: str = "morning", escalate: bool = True) -> dict[str, Any]:
        kind = kind if kind in REVIEW_KINDS else "morning"
        if escalate:
            self.escalate_delayed_followups()
        review = self.review(escalate=escalate)
        tasks = self.list_tasks(statuses=["pending", "active", "blocked"], limit=200)
        communications = self.list_communications(limit=200)
        scored = sorted(tasks, key=self.urgency_score_task, reverse=True)
        now = datetime.utcnow()

        pending_comms = [
            msg for msg in communications
            if msg["delivery_status"] in {"draft", "ready"} or (msg["follow_up_required"] and msg.get("follow_up_due"))
        ]
        completed_recent = [
            task for task in self.list_tasks(statuses=["completed"], include_archived=True, limit=100)
            if parse_iso(task.get("updated_at")) and parse_iso(task.get("updated_at")) >= now - timedelta(hours=24)
        ]
        tomorrow = [
            task for task in tasks
            if parse_iso(task.get("due_date")) and now <= parse_iso(task.get("due_date")) <= now + timedelta(hours=36)
        ]
        ignored = [
            task for task in tasks
            if int(task.get("escalation_level") or 0) >= 2 or (task.get("reminder_state") or {}).get("last_sent_at")
        ]

        if kind == "night":
            sections = {
                "completed": completed_recent[:5],
                "missed_commitments": review["overdue"][:5],
                "ignored_reminders": ignored[:5],
                "carry_forward": scored[:5],
                "tomorrow_focus": sorted(tomorrow, key=self.urgency_score_task, reverse=True)[:3],
            }
        elif kind == "weekly":
            slipped = [
                task for task in tasks
                if parse_iso(task.get("due_date")) and parse_iso(task.get("due_date")) < now
            ]
            sections = {
                "slipped_this_week": slipped[:7],
                "stale_or_avoided": review["stale"][:7],
                "blocked": review["blocked"][:5],
                "communication_followups": pending_comms[:5],
                "next_week_top": scored[:5],
            }
        else:
            sections = {
                "unfinished": tasks[:7],
                "missed_deadlines": review["overdue"][:5],
                "urgent_followups": due_communications(pending_comms, hours=48)[:5],
                "important_communication_pending": pending_comms[:5],
                "top_3_today": scored[:3],
            }

        text = format_executive_review(kind, sections)
        return {
            "kind": kind,
            "summary": text,
            "sections": sections,
            "generated_at": utc_now(),
        }

    def record_scheduler_event(
        self,
        event_type: str,
        target_id: str | None = None,
        payload: dict[str, Any] | None = None,
        delivered: bool = False,
    ) -> dict[str, Any]:
        event = {
            "event_id": f"sched-{uuid.uuid4().hex[:12]}",
            "event_type": event_type,
            "target_id": target_id,
            "payload": payload or {},
            "delivered_at": utc_now() if delivered else None,
            "created_at": utc_now(),
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_events (event_id, event_type, target_id, payload, delivered_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["event_type"],
                    event["target_id"],
                    json_dump(event["payload"]),
                    event["delivered_at"],
                    event["created_at"],
                ),
            )
        return event

    def last_scheduler_delivery(self, event_type: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM scheduler_events
                WHERE event_type = ? AND delivered_at IS NOT NULL
                ORDER BY delivered_at DESC
                LIMIT 1
                """,
                (event_type,),
            ).fetchone()
        if not row:
            return None
        return {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "target_id": row["target_id"],
            "payload": json_load(row["payload"], {}),
            "delivered_at": row["delivered_at"],
            "created_at": row["created_at"],
        }

    def due_review_kinds(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.utcnow()
        config = self.get_scheduler_config()
        if not config.get("enabled", True):
            return []
        due = []
        for kind in ("morning", "night", "weekly"):
            if kind == "weekly" and now.weekday() != int(config.get("weekly_review_weekday", 6)):
                continue
            window = config.get(f"{kind}_review_window", {})
            if not within_window(now, window):
                continue
            last = self.last_scheduler_delivery(f"{kind}_review")
            if last and same_delivery_period(kind, parse_iso(last.get("delivered_at")), now):
                continue
            due.append(kind)
        return due

    def escalate_delayed_followups(self) -> list[dict[str, Any]]:
        now = datetime.utcnow()
        delayed = []
        messages = self.list_communications(pending_follow_up=True, limit=200)
        for message in messages:
            due = parse_iso(message.get("follow_up_due"))
            if not due or due >= now:
                continue
            delayed.append(message)
            related_task_id = message.get("related_task_id")
            if related_task_id:
                task = self.get_task(related_task_id)
                if task:
                    self.update_task(
                        related_task_id,
                        {
                            "escalation_level": min(5, int(task.get("escalation_level") or 0) + 1),
                            "follow_up_state": {
                                **(task.get("follow_up_state") or {}),
                                "state": "delayed_follow_up",
                                "message_id": message["message_id"],
                                "last_escalated_at": utc_now(),
                            },
                        },
                    )
        return delayed


def row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "title": row["title"],
        "context": row["context"],
        "owner": row["owner"],
        "due_date": row["due_date"],
        "recurrence": row["recurrence"],
        "priority": row["priority"],
        "status": row["status"],
        "follow_up_state": json_load(row["follow_up_state"], {}),
        "reminder_state": json_load(row["reminder_state"], {}),
        "escalation_level": row["escalation_level"],
        "approval_required": bool(row["approval_required"]),
        "approval_status": row["approval_status"],
        "related_people": json_load(row["related_people"], []),
        "communication_history": json_load(row["communication_history"], []),
        "source": row["source"],
        "last_reviewed_at": row["last_reviewed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_communication(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "message_id": row["message_id"],
        "recipient": row["recipient"],
        "channel": row["channel"],
        "subject": row["subject"],
        "draft_content": row["draft_content"],
        "tone_profile": row["tone_profile"],
        "approval_required": bool(row["approval_required"]),
        "approval_status": row["approval_status"],
        "follow_up_required": bool(row["follow_up_required"]),
        "follow_up_due": row["follow_up_due"],
        "related_task_id": row["related_task_id"],
        "communication_history": json_load(row["communication_history"], []),
        "delivery_status": row["delivery_status"],
        "last_sent_at": row["last_sent_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def normalize_channel(value: Any) -> str:
    channel = str(value or "draft").strip().lower()
    return channel if channel in COMMUNICATION_CHANNELS else "draft"


def normalize_delivery_status(value: Any) -> str:
    status = str(value or "draft").strip().lower()
    return status if status in DELIVERY_STATUSES else "draft"


def default_scheduler_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "morning_review_window": {"start": "07:00", "end": "10:00"},
        "night_review_window": {"start": "20:00", "end": "23:30"},
        "weekly_review_window": {"start": "09:00", "end": "12:00"},
        "weekly_review_weekday": 6,
        "due_soon_hours": 24,
        "stale_after_days": 7,
        "max_daily_reminders": 3,
        "telegram_delivery_enabled": True,
        "accountability_escalation_threshold": 2,
    }


def within_window(now: datetime, window: dict[str, Any]) -> bool:
    start = parse_hhmm(str(window.get("start") or "00:00"))
    end = parse_hhmm(str(window.get("end") or "23:59"))
    current = now.hour * 60 + now.minute
    return start <= current <= end


def parse_hhmm(value: str) -> int:
    try:
        hour, minute = value.split(":", 1)
        return max(0, min(23, int(hour))) * 60 + max(0, min(59, int(minute)))
    except Exception:
        return 0


def same_delivery_period(kind: str, last: datetime | None, now: datetime) -> bool:
    if not last:
        return False
    if kind == "weekly":
        return last.isocalendar()[:2] == now.isocalendar()[:2]
    return last.date() == now.date()


def due_communications(messages: list[dict[str, Any]], hours: int = 48) -> list[dict[str, Any]]:
    now = datetime.utcnow()
    result = []
    for message in messages:
        due = parse_iso(message.get("follow_up_due"))
        if due and due <= now + timedelta(hours=hours):
            result.append(message)
    return result


def format_executive_review(kind: str, sections: dict[str, list[dict[str, Any]]]) -> str:
    title = {
        "morning": "AJA morning review",
        "night": "AJA night review",
        "weekly": "AJA weekly review",
    }.get(kind, "AJA review")
    lines = [title]

    if kind == "morning":
        lines.extend(
            [
                section_lines("Top 3 today", sections.get("top_3_today", []), task_line),
                section_lines("Missed deadlines", sections.get("missed_deadlines", []), task_line),
                section_lines("Urgent follow-ups", sections.get("urgent_followups", []), message_line),
                section_lines("Communication pending", sections.get("important_communication_pending", []), message_line),
                accountability_line(sections.get("missed_deadlines", []) + sections.get("top_3_today", [])),
            ]
        )
    elif kind == "night":
        lines.extend(
            [
                section_lines("Completed today", sections.get("completed", []), task_line),
                section_lines("Missed commitments", sections.get("missed_commitments", []), task_line),
                section_lines("Ignored reminders", sections.get("ignored_reminders", []), task_line),
                section_lines("Carry forward", sections.get("carry_forward", []), task_line),
                section_lines("Tomorrow focus", sections.get("tomorrow_focus", []), task_line),
                accountability_line(sections.get("missed_commitments", []) + sections.get("ignored_reminders", [])),
            ]
        )
    else:
        lines.extend(
            [
                section_lines("Slipped this week", sections.get("slipped_this_week", []), task_line),
                section_lines("Avoidance patterns", sections.get("stale_or_avoided", []), task_line),
                section_lines("Blocked", sections.get("blocked", []), task_line),
                section_lines("Communication follow-ups", sections.get("communication_followups", []), message_line),
                section_lines("Next week top", sections.get("next_week_top", []), task_line),
                accountability_line(sections.get("slipped_this_week", []) + sections.get("stale_or_avoided", [])),
            ]
        )

    compact = [line for line in lines if line]
    return "\n\n".join(compact)


def section_lines(title: str, items: list[dict[str, Any]], formatter) -> str:
    if not items:
        return f"{title}: none"
    lines = [f"{title}:"]
    for item in items[:5]:
        lines.append(f"- {formatter(item)}")
    return "\n".join(lines)


def task_line(task: dict[str, Any]) -> str:
    due = task.get("due_date") or "no due"
    escalation = int(task.get("escalation_level") or 0)
    suffix = f", E{escalation}" if escalation else ""
    return f"{task.get('title')} [{task.get('priority')}, {task.get('status')}, due {due}{suffix}]"


def message_line(message: dict[str, Any]) -> str:
    due = message.get("follow_up_due") or "no follow-up due"
    return f"{message.get('recipient')} - {message.get('subject') or 'message'} [{message.get('delivery_status')}, follow-up {due}]"


def accountability_line(items: list[dict[str, Any]]) -> str:
    escalated = [item for item in items if int(item.get("escalation_level") or 0) >= 2]
    if escalated:
        return "Accountability: You said this mattered. Either do it today or remove it honestly."
    if items:
        return "Accountability: Pick the next concrete action. Do not let this stay vague."
    return ""


def normalize_due_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat() + "Z"
    text = str(value).strip()
    lowered = text.lower()
    now = datetime.utcnow()
    if lowered == "today":
        return now.replace(hour=18, minute=0, second=0, microsecond=0).isoformat() + "Z"
    if lowered == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat() + "Z"
    if lowered.startswith("next "):
        weekday = lowered.replace("next ", "", 1).strip()
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        if weekday in weekdays:
            target = weekdays.index(weekday)
            days = (target - now.weekday() + 7) % 7
            days = 7 if days == 0 else days
            return (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat() + "Z"
    parsed = parse_iso(text)
    if parsed:
        return parsed.replace(microsecond=0).isoformat() + "Z"
    return text


def normalize_recurrence(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, dict):
        freq = str(value.get("freq") or value.get("frequency") or "").lower()
        if freq not in {"daily", "weekly", "monthly", "yearly"}:
            return None
        interval = max(1, int(value.get("interval") or 1))
        return json.dumps({"freq": freq, "interval": interval}, ensure_ascii=True)
    text = str(value).strip().lower()
    if text in {"daily", "weekly", "monthly", "yearly"}:
        return json.dumps({"freq": text, "interval": 1}, ensure_ascii=True)
    try:
        parsed = json.loads(text)
        return normalize_recurrence(parsed)
    except Exception:
        return None


def parse_recurrence(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
        if data.get("freq") in {"daily", "weekly", "monthly", "yearly"}:
            data["interval"] = max(1, int(data.get("interval") or 1))
            return data
    except Exception:
        return None
    return None


def next_recurrence_date(current_due: str | None, recurrence: dict[str, Any]) -> str:
    base = parse_iso(current_due) or datetime.utcnow()
    interval = int(recurrence.get("interval") or 1)
    freq = recurrence.get("freq")
    if freq == "daily":
        next_due = base + timedelta(days=interval)
    elif freq == "weekly":
        next_due = base + timedelta(weeks=interval)
    elif freq == "monthly":
        next_due = base + timedelta(days=30 * interval)
    elif freq == "yearly":
        next_due = base + timedelta(days=365 * interval)
    else:
        next_due = base
    return next_due.replace(microsecond=0).isoformat() + "Z"


def parse_task_intent(text: str, source: str = "Telegram", owner: str = "AJA") -> dict[str, Any] | None:
    raw = " ".join((text or "").strip().split())
    lowered = raw.lower()
    if not raw:
        return None

    title = raw
    due_date = None
    priority = "medium"
    recurrence = None
    context = raw
    follow_up_state = {"state": "not_started"}
    reminder_state = {"enabled": False, "last_sent_at": None}
    related_people: list[str] = []

    if lowered.startswith("add task "):
        title = raw[9:].strip()
    elif lowered.startswith("remember "):
        title = raw[9:].strip()
    elif lowered.startswith("remind me "):
        title = raw
        reminder_state["enabled"] = True
    elif lowered.startswith("follow up "):
        title = raw
        follow_up_state = {"state": "needs_follow_up"}
    else:
        obligation_starters = ("remind me", "follow up", "check ", "pay ", "bill ", "project ", "internship ")
        if not lowered.startswith(obligation_starters):
            return None

    for label in ("urgent", "high", "medium", "low"):
        if re.search(rf"\bpriority\s+{label}\b", lowered) or re.search(rf"\b{label}\s+priority\b", lowered):
            priority = label
            title = re.sub(rf"\bpriority\s+{label}\b", "", title, flags=re.IGNORECASE).strip()
            title = re.sub(rf"\b{label}\s+priority\b", "", title, flags=re.IGNORECASE).strip()
            break

    due_match = re.search(r"\bdue\s+(.+?)(?:\s+priority\b|\s+every\b|$)", raw, flags=re.IGNORECASE)
    if due_match:
        due_date = due_match.group(1).strip()
        title = re.sub(r"\bdue\s+.+?(?:\s+priority\b.*|$)", "", title, flags=re.IGNORECASE).strip()
    else:
        next_match = re.search(r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", raw, flags=re.IGNORECASE)
        if next_match:
            due_date = f"next {next_match.group(1)}"
        elif "tomorrow" in lowered:
            due_date = "tomorrow"
        elif "today" in lowered:
            due_date = "today"

    recurrence_match = re.search(r"\bevery\s+(day|daily|week|weekly|month|monthly|year|yearly)\b", lowered)
    if recurrence_match:
        token = recurrence_match.group(1)
        recurrence = {"day": "daily", "week": "weekly", "month": "monthly", "year": "yearly"}.get(token, token)
        title = re.sub(r"\bevery\s+(day|daily|week|weekly|month|monthly|year|yearly)\b", "", title, flags=re.IGNORECASE).strip()

    people_match = re.search(r"\bwith\s+([A-Z][A-Za-z0-9_-]+)", raw)
    if people_match:
        related_people.append(people_match.group(1))

    return {
        "title": title or raw,
        "context": context,
        "owner": owner,
        "due_date": due_date,
        "recurrence": recurrence,
        "priority": priority,
        "status": "pending",
        "follow_up_state": follow_up_state,
        "reminder_state": reminder_state,
        "related_people": related_people,
        "source": source,
        "communication_history": [{"at": utc_now(), "type": "source", "message": raw}],
    }


def parse_communication_intent(text: str, source: str = "Telegram") -> dict[str, Any] | None:
    raw = " ".join((text or "").strip().split())
    lowered = raw.lower()
    if not raw:
        return None

    is_draft = lowered.startswith("draft ") or lowered.startswith("send ")
    is_reminder = lowered.startswith("remind ") and not lowered.startswith("remind me")
    if not is_draft and not is_reminder:
        return None

    channel = "email" if "email" in lowered or "recruiter" in lowered or "professional" in lowered else "telegram"
    tone = "professional" if any(word in lowered for word in ["recruiter", "internship", "professional", "email"]) else "friendly"
    follow_up_required = any(word in lowered for word in ["recruiter", "internship", "follow-up", "follow up"])
    follow_up_due = "next tuesday" if "next tuesday" in lowered else None
    recipient = "recipient"
    subject = "Follow-up"
    context = raw

    recruiter_match = re.search(r"\brecruiter\b(?:\s+([A-Z][A-Za-z0-9_-]+))?", raw)
    if recruiter_match:
        recipient = recruiter_match.group(1) or "recruiter"
        subject = "Follow-up on opportunity"
    elif "internship" in lowered:
        recipient = "recruiter"
        subject = "Internship application follow-up"
    elif is_reminder:
        parts = raw.split(maxsplit=2)
        recipient = parts[1] if len(parts) > 1 else "recipient"
        subject = "Reminder"
        context = parts[2] if len(parts) > 2 else raw
    else:
        to_match = re.search(r"\bto\s+([A-Za-z0-9_@.+-]+)", raw, flags=re.IGNORECASE)
        if to_match:
            recipient = to_match.group(1)

    if lowered.startswith("draft professional reply to recruiter"):
        subject = "Professional reply"
        recipient = "recruiter"
        draft = professional_reply_draft()
    elif "recruiter" in lowered and "follow" in lowered:
        draft = recruiter_follow_up_draft()
    elif "internship" in lowered:
        draft = internship_follow_up_draft()
    elif is_reminder:
        draft = reminder_draft(recipient, context)
    else:
        draft = generic_draft(recipient, context, tone)

    return {
        "recipient": recipient,
        "channel": channel,
        "subject": subject,
        "draft_content": draft,
        "tone_profile": tone,
        "approval_required": True,
        "approval_status": "pending",
        "follow_up_required": follow_up_required,
        "follow_up_due": follow_up_due,
        "source": source,
        "communication_history": [{"at": utc_now(), "type": "source", "message": raw}],
    }


def recruiter_follow_up_draft() -> str:
    return (
        "Hi,\n\n"
        "I hope you are doing well. I wanted to follow up on our recent conversation and check whether "
        "there are any updates on the role or next steps. I remain interested and would be happy to share "
        "any additional information that would be useful.\n\n"
        "Best regards"
    )


def internship_follow_up_draft() -> str:
    return (
        "Hello,\n\n"
        "I hope you are doing well. I am writing to follow up on my internship application and ask whether "
        "there are any updates regarding the status or next steps. I appreciate your time and consideration.\n\n"
        "Best regards"
    )


def professional_reply_draft() -> str:
    return (
        "Hello,\n\n"
        "Thank you for reaching out. I appreciate the update and would be glad to discuss the opportunity "
        "further. Please let me know a convenient time or the next steps you would like me to complete.\n\n"
        "Best regards"
    )


def reminder_draft(recipient: str, context: str) -> str:
    return f"Hi {recipient}, just a quick reminder about {context}. Please let me know if you need anything from me."


def generic_draft(recipient: str, context: str, tone: str) -> str:
    greeting = f"Hi {recipient},"
    if tone == "professional":
        return f"{greeting}\n\nI wanted to follow up regarding {context}. Please let me know the best next step.\n\nBest regards"
    return f"{greeting} just checking in about {context}."


def format_communication_for_mobile(message: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Draft saved",
            f"ID: {message['message_id']}",
            f"To: {message['recipient']}",
            f"Channel: {message['channel']}",
            f"Subject: {message.get('subject') or '(none)'}",
            f"Tone: {message['tone_profile']}",
            f"Approval: {message['approval_status']}",
            f"Follow-up: {'yes' if message['follow_up_required'] else 'no'}",
            "",
            "Draft:",
            message["draft_content"],
            "",
            f"Approve: approve message {message['message_id']}",
            f"Edit: edit message {message['message_id']} <new text>",
            f"Send after approval: send message {message['message_id']}",
        ]
    )


def format_communications_for_mobile(
    drafts: list[dict[str, Any]],
    ready: list[dict[str, Any]],
    followups: list[dict[str, Any]],
) -> str:
    lines = ["AJA communication summary"]
    lines.append(f"Drafts awaiting approval: {len(drafts)}")
    lines.append(f"Approved ready to send: {len(ready)}")
    lines.append(f"Follow-ups tracked: {len(followups)}")
    items = [*drafts[:5], *ready[:5], *followups[:5]]
    if not items:
        lines.extend(["", "No pending communications."])
        return "\n".join(lines)
    lines.append("")
    seen = set()
    for message in items:
        if message["message_id"] in seen:
            continue
        seen.add(message["message_id"])
        lines.append(
            f"- {message['message_id']} -> {message['recipient']} "
            f"({message['delivery_status']}, approval {message['approval_status']})"
        )
    return "\n".join(lines)


def format_tasks_for_mobile(tasks: list[dict[str, Any]], review: dict[str, Any] | None = None) -> str:
    review = review or {}
    lines = ["AJA secretary summary"]
    lines.append(f"Active: {review.get('active_count', len(tasks))}")
    lines.append(f"Overdue: {len(review.get('overdue', []))}")
    lines.append(f"Due soon: {len(review.get('due_soon', []))}")
    lines.append(f"Stale: {len(review.get('stale', []))}")
    if not tasks:
        lines.append("")
        lines.append("No active obligations.")
        return "\n".join(lines)

    lines.append("")
    for task in tasks[:10]:
        due = task.get("due_date") or "no due date"
        escalation = int(task.get("escalation_level") or 0)
        escalation_text = f" E{escalation}" if escalation else ""
        lines.append(
            f"- [{task.get('priority')}] {task.get('title')} ({task.get('status')}, due {due})"
            f"{escalation_text}\n  id: {task.get('task_id')}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Worker Registry helpers
# ---------------------------------------------------------------------------

def _row_to_worker(row: Any) -> dict[str, Any]:
    """Convert a sqlite3.Row from worker_registry to a typed dict."""
    d = dict(row)
    for key in ("primary_strengths", "weak_areas", "preferred_task_types", "blocked_task_types",
                "recommended_use_cases", "known_failure_patterns", "recent_failures"):
        d[key] = json_load(d.get(key), [])
    d["metadata"] = json_load(d.get("metadata"), {})
    for bool_key in ("supports_tests", "supports_git_operations", "supports_deployment",
                     "supports_plan_mode", "requires_manual_review"):
        d[bool_key] = bool(d.get(bool_key))
    return d


def _default_worker_profiles() -> list[dict[str, Any]]:
    """Return the 7 initial worker profiles for seeding the registry."""
    return [
        {
            "worker_id": "github-copilot-cli",
            "worker_name": "GitHub Copilot CLI",
            "worker_type": "cli_agent",
            "availability_status": "available",
            "primary_strengths": ["code generation", "inline completion", "plan mode", "git operations", "PR creation", "code review"],
            "weak_areas": ["long-running autonomous tasks", "multi-file refactoring without context"],
            "preferred_task_types": ["code", "fix", "refactor", "test", "review", "git", "pr"],
            "blocked_task_types": [],
            "execution_speed": "fast",
            "reliability_score": 0.88,
            "cost_profile": "subscription",
            "approval_risk_level": "low",
            "supports_tests": True,
            "supports_git_operations": True,
            "supports_deployment": False,
            "supports_plan_mode": True,
            "requires_manual_review": True,
            "historical_success_rate": 0.0,
            "recommended_use_cases": ["Quick code fixes", "PR generation", "Test writing", "Code review assist", "Plan-mode debugging"],
            "known_failure_patterns": ["May hallucinate file paths in unfamiliar repos", "Context window overflow on large codebases"],
        },
        {
            "worker_id": "gemini-cli",
            "worker_name": "Gemini CLI",
            "worker_type": "cli_agent",
            "availability_status": "available",
            "primary_strengths": ["large context window", "multimodal understanding", "research", "documentation", "code generation", "analysis"],
            "weak_areas": ["shell command execution safety", "production deployment experience"],
            "preferred_task_types": ["code", "research", "documentation", "analysis", "refactor", "test", "review"],
            "blocked_task_types": [],
            "execution_speed": "medium",
            "reliability_score": 0.85,
            "cost_profile": "subscription",
            "approval_risk_level": "medium",
            "supports_tests": True,
            "supports_git_operations": True,
            "supports_deployment": False,
            "supports_plan_mode": True,
            "requires_manual_review": True,
            "historical_success_rate": 0.0,
            "recommended_use_cases": ["Deep analysis", "Large-file refactoring", "Documentation generation", "Research tasks", "Complex debugging"],
            "known_failure_patterns": ["May over-generate when task scope is vague", "Occasional formatting inconsistencies"],
        },
        {
            "worker_id": "claude-code",
            "worker_name": "Claude Code",
            "worker_type": "cli_agent",
            "availability_status": "unavailable",
            "primary_strengths": ["autonomous multi-step execution", "plan mode", "test-driven development", "git operations", "code review"],
            "weak_areas": ["requires API key or subscription", "cost per token can be high"],
            "preferred_task_types": ["code", "fix", "refactor", "test", "deploy", "research", "review", "pr"],
            "blocked_task_types": [],
            "execution_speed": "medium",
            "reliability_score": 0.90,
            "cost_profile": "pay_per_use",
            "approval_risk_level": "medium",
            "supports_tests": True,
            "supports_git_operations": True,
            "supports_deployment": True,
            "supports_plan_mode": True,
            "requires_manual_review": True,
            "historical_success_rate": 0.0,
            "recommended_use_cases": ["Complex multi-file features", "End-to-end debugging", "TDD workflows", "Deployment automation"],
            "known_failure_patterns": ["Token cost escalation on large tasks", "May need explicit permission grants"],
        },
        {
            "worker_id": "aider",
            "worker_name": "Aider",
            "worker_type": "cli_agent",
            "availability_status": "unavailable",
            "primary_strengths": ["git-native editing", "pair programming", "incremental changes", "multi-model support"],
            "weak_areas": ["no plan mode", "limited autonomous operation", "requires model API key"],
            "preferred_task_types": ["code", "fix", "refactor"],
            "blocked_task_types": ["deploy", "research"],
            "execution_speed": "fast",
            "reliability_score": 0.82,
            "cost_profile": "pay_per_use",
            "approval_risk_level": "low",
            "supports_tests": True,
            "supports_git_operations": True,
            "supports_deployment": False,
            "supports_plan_mode": False,
            "requires_manual_review": True,
            "historical_success_rate": 0.0,
            "recommended_use_cases": ["Pair programming sessions", "Small targeted fixes", "Git-native refactors"],
            "known_failure_patterns": ["Struggles with large architectural changes", "Can loop on ambiguous instructions"],
        },
        {
            "worker_id": "codex-cli",
            "worker_name": "Codex CLI",
            "worker_type": "cli_agent",
            "availability_status": "unavailable",
            "primary_strengths": ["sandboxed execution", "autonomous task completion", "safety-first design"],
            "weak_areas": ["limited model selection", "OpenAI API dependency", "newer tool with less community validation"],
            "preferred_task_types": ["code", "fix", "test", "research"],
            "blocked_task_types": [],
            "execution_speed": "medium",
            "reliability_score": 0.78,
            "cost_profile": "pay_per_use",
            "approval_risk_level": "low",
            "supports_tests": True,
            "supports_git_operations": True,
            "supports_deployment": False,
            "supports_plan_mode": False,
            "requires_manual_review": True,
            "historical_success_rate": 0.0,
            "recommended_use_cases": ["Safe exploratory coding", "Sandboxed experiments", "Quick prototyping"],
            "known_failure_patterns": ["Sandbox limitations may block certain file operations", "Model availability constraints"],
        },
        {
            "worker_id": "opencode",
            "worker_name": "OpenCode",
            "worker_type": "cli_agent",
            "availability_status": "unavailable",
            "primary_strengths": ["TUI-based interaction", "multi-provider support", "LSP integration"],
            "weak_areas": ["newer tool", "smaller community", "limited autonomous mode"],
            "preferred_task_types": ["code", "fix", "refactor"],
            "blocked_task_types": ["deploy"],
            "execution_speed": "medium",
            "reliability_score": 0.75,
            "cost_profile": "pay_per_use",
            "approval_risk_level": "low",
            "supports_tests": True,
            "supports_git_operations": True,
            "supports_deployment": False,
            "supports_plan_mode": False,
            "requires_manual_review": True,
            "historical_success_rate": 0.0,
            "recommended_use_cases": ["Interactive code editing", "Multi-provider experimentation"],
            "known_failure_patterns": ["May require manual TUI interaction", "Provider-specific quirks"],
        },
        {
            "worker_id": "swarm-maintenance",
            "worker_name": "Swarm Maintenance Worker",
            "worker_type": "internal_agent",
            "availability_status": "available",
            "primary_strengths": ["self-healing", "background monitoring", "codebase health", "parallel execution"],
            "weak_areas": ["no creative coding", "no deployment authority", "limited to predefined repair patterns"],
            "preferred_task_types": ["maintenance", "health_check", "monitoring", "repair"],
            "blocked_task_types": ["deploy", "pr", "research"],
            "execution_speed": "fast",
            "reliability_score": 0.92,
            "cost_profile": "free",
            "approval_risk_level": "low",
            "supports_tests": False,
            "supports_git_operations": False,
            "supports_deployment": False,
            "supports_plan_mode": False,
            "requires_manual_review": False,
            "historical_success_rate": 0.0,
            "recommended_use_cases": ["Background codebase health", "Automated repairs", "Resource monitoring"],
            "known_failure_patterns": ["Cannot handle novel/creative tasks", "Limited to pattern-based repairs"],
        },
    ]

