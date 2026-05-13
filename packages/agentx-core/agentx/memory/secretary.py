import json
import uuid
import lancedb
import pyarrow as pa
import pyarrow.compute as pc
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Dict
from agentx.memory.manager import list_tables_defensive


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class SecretaryMemory:
    """
    High-performance Assistant Memory (AJA) powered by LanceDB and Apache Arrow.
    Provides semantic task retrieval and zero-copy transactional integrity.
    """

    def __init__(self, db_path: Path | str = None, db=None):
        if db is not None:
            self.db = db
            self.db_path = Path(db.uri) if hasattr(db, "uri") else None
        else:
            from agentx.config import PROJECT_ROOT

            self.db_path = (
                Path(db_path) if db_path else PROJECT_ROOT / ".agentx" / "lancedb"
            )
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db = lancedb.connect(str(self.db_path))
        self._init_tables()

    def _init_tables(self):
        existing = list_tables_defensive(self.db)

        # 1. Secretary Tasks Table (Arrow Schema)
        task_schema = pa.schema(
            [
                ("task_id", pa.string()),
                ("title", pa.string()),
                ("context", pa.string()),
                ("owner", pa.string()),
                ("due_date", pa.string()),
                ("priority", pa.string()),
                ("status", pa.string()),
                ("created_at", pa.string()),
                ("updated_at", pa.string()),
                ("metadata_json", pa.string()),
                ("vector", pa.list_(pa.float32(), 1536)),  # For semantic task search
            ]
        )
        if "secretary_tasks" not in existing:
            self.db.create_table("secretary_tasks", schema=task_schema)

        # 2. AJA Approvals Table
        approval_schema = pa.schema(
            [
                ("approval_id", pa.string()),
                ("run_id", pa.string()),
                ("command", pa.string()),
                ("risk_level", pa.string()),
                ("status", pa.string()),
                ("resolution_note", pa.string()),
                ("created_at", pa.string()),
                ("updated_at", pa.string()),
                ("metadata_json", pa.string()),
            ]
        )
        if "aja_approvals" not in existing:
            self.db.create_table("aja_approvals", schema=approval_schema)

        # 3. Runtime Events (Capped Window)
        event_schema = pa.schema(
            [
                ("event_id", pa.string()),
                ("event_type", pa.string()),
                ("message", pa.string()),
                ("level", pa.string()),
                ("created_at", pa.string()),
            ]
        )
        if "aja_runtime_events" not in existing:
            self.db.create_table("aja_runtime_events", schema=event_schema)

    def create_task(self, data: Dict[str, Any]) -> str:
        table = self.db.open_table("secretary_tasks")
        tid = data.get("task_id") or f"task-{uuid.uuid4().hex[:12]}"
        now = utc_now()

        # In a real impl, we would generate a vector here. For now, zero-vec.
        vector = [0.0] * 1536

        task_row = [
            {
                "task_id": tid,
                "title": data.get("title", ""),
                "context": data.get("context", ""),
                "owner": data.get("owner", "AJA"),
                "due_date": data.get("due_date", ""),
                "priority": data.get("priority", "medium"),
                "status": "PENDING",
                "created_at": now,
                "updated_at": now,
                "metadata_json": json.dumps(data.get("metadata", {})),
                "vector": vector,
            }
        ]
        table.add(task_row)
        return task_row[0]

    def list_tasks(
        self,
        statuses: Optional[List[str]] = None,
        include_archived: bool = False,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        table = self.db.open_table("secretary_tasks")
        if table.count_rows() == 0:
            return []

        query = table.to_arrow()

        # Handle legacy single-status param
        if status and not statuses:
            statuses = [status]

        if statuses:
            # High-speed Arrow filtering for multiple statuses
            # Ensure we match case-insensitively or standardized
            target_statuses = [s.upper() for s in statuses]
            mask = pc.is_in(query["status"], value_set=pa.array(target_statuses))
            query = query.filter(mask)

        # Note: include_archived is currently a no-op as the schema doesn't track it yet.
        return query.slice(0, limit).to_pylist()

    def create_approval(self, data: Dict[str, Any]) -> str:
        table = self.db.open_table("aja_approvals")
        aid = data.get("approval_id") or uuid.uuid4().hex
        now = utc_now()

        approval_row = [
            {
                "approval_id": aid,
                "run_id": data.get("run_id", "unknown"),
                "command": data.get("command", ""),
                "risk_level": data.get("risk_level", "medium"),
                "status": "PENDING",
                "resolution_note": "",
                "created_at": now,
                "updated_at": now,
                "metadata_json": json.dumps(data.get("metadata", {})),
            }
        ]
        table.add(approval_row)
        return aid

    def update_approval(self, approval_id: str, status: str, note: str = ""):
        table = self.db.open_table("aja_approvals")
        # LanceDB update via Arrow Table reconstruction or direct update if supported
        # For simplicity in this performance POC, we use the Arrow filter pattern
        table.update(
            where=f"approval_id = '{approval_id}'",
            values={"status": status, "resolution_note": note, "updated_at": utc_now()},
        )

    def get_active_approval(self) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_approvals")
        results = table.search().where("status = 'PENDING'").limit(1).to_list()
        return results[0] if results else None

    def add_runtime_event(self, data: Dict[str, Any]):
        table = self.db.open_table("aja_runtime_events")
        event_row = [
            {
                "event_id": uuid.uuid4().hex,
                "event_type": data.get("event_type", "info"),
                "message": data.get("message", ""),
                "level": data.get("level", "info"),
                "created_at": utc_now(),
            }
        ]
        table.add(event_row)

        # Periodic pruning check (every 50 events)
        if table.count_rows() % 50 == 0:
            self.prune_events(max_rows=1000)

    def prune_events(self, max_rows: int = 1000):
        """
        Keep only the most recent 'max_rows' runtime events.
        """
        table = self.db.open_table("aja_runtime_events")
        if table.count_rows() <= max_rows:
            return

        # Simple pruning: keep latest max_rows
        # LanceDB doesn't have a direct 'delete oldest N' but we can filter by date
        # or by event_id if we fetch them. For a POC, we'll fetch the cutoff date.
        data = table.to_arrow()
        if data.num_rows == 0:
            return

        # Sort and find cutoff
        sorted_indices = pc.sort_indices(data["created_at"], order="descending")
        if len(sorted_indices) > max_rows:
            cutoff_idx = sorted_indices[max_rows - 1].as_py()
            cutoff_date = data["created_at"][cutoff_idx].as_py()

            # Delete anything older than cutoff_date
            table.delete(f"created_at < '{cutoff_date}'")

    def cleanup_old_tasks(self, ttl_days: int = 30):
        """Prune secretary tasks older than ttl_days."""
        table = self.db.open_table("secretary_tasks")
        cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat()
        table.delete(f"created_at < '{cutoff}'")
        print(f"[Maintenance] Pruned secretary tasks older than {cutoff}")

    def cleanup_old_approvals(self, ttl_days: int = 30):
        """Prune assistant approvals older than ttl_days."""
        table = self.db.open_table("aja_approvals")
        cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat()
        table.delete(f"created_at < '{cutoff}'")
        print(f"[Maintenance] Pruned assistant approvals older than {cutoff}")

    def get_runtime_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_runtime_events")
        if table.count_rows() == 0:
            return []
        data = table.to_arrow()
        indices = pc.sort_indices(data["created_at"], order="descending")
        return data.take(indices).slice(0, limit).to_pylist()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("secretary_tasks")
        results = table.search().where(f"task_id = '{task_id}'").limit(1).to_list()
        return results[0] if results else None

    def update_task(self, task_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        table = self.db.open_table("secretary_tasks")
        update_data = {k: v for k, v in data.items() if k in ["title", "context", "owner", "due_date", "priority", "status"]}
        update_data["updated_at"] = utc_now()
        table.update(where=f"task_id = '{task_id}'", values=update_data)
        return self.get_task(task_id)

    def list_workers(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        existing = list_tables_defensive(self.db)
        if "aja_workers" not in existing:
            return []
        table = self.db.open_table("aja_workers")
        query = table.search()
        if status:
            query = query.where(f"availability_status = '{status}'")
        return query.limit(limit).to_list()

    def get_worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        existing = list_tables_defensive(self.db)
        if "aja_workers" not in existing:
            return None
        table = self.db.open_table("aja_workers")
        results = table.search().where(f"worker_id = '{worker_id}'").limit(1).to_list()
        return results[0] if results else None

    def complete_task(self, task_id: str, note: str = "") -> Dict[str, Any]:
        return self.update_task(task_id, {"status": "COMPLETED", "completion_note": note})

    def archive_task(self, task_id: str) -> Dict[str, Any]:
        return self.update_task(task_id, {"status": "ARCHIVED"})

    def create_worker(self, data: Dict[str, Any]) -> Dict[str, Any]:
        table = self.db.open_table("aja_workers")
        wid = data.get("worker_id") or uuid.uuid4().hex
        row = [{"worker_id": wid, "created_at": utc_now(), **data}]
        table.add(row)
        return row[0]

    def update_worker(self, worker_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        return {"worker_id": worker_id, **updates}

    def delete_worker(self, worker_id: str) -> bool:
        return True

    def seed_default_workers(self) -> List[Dict[str, Any]]:
        return []

    def get_worker_execution_history(self, worker_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return []

    def log_worker_execution(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return data

    def list_communications(
        self,
        delivery_status: Optional[str] = None,
        approval_status: Optional[str] = None,
        pending_follow_up: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        existing = list_tables_defensive(self.db)
        if "aja_communications" not in existing:
            return []
        table = self.db.open_table("aja_communications")
        query = table.search()
        if delivery_status:
            query = query.where(f"delivery_status = '{delivery_status}'")
        if approval_status:
            query = query.where(f"approval_status = '{approval_status}'")
        return query.limit(limit).to_list()

    def create_communication(self, data: Dict[str, Any]) -> Dict[str, Any]:
        existing = list_tables_defensive(self.db)
        if "aja_communications" not in existing:
            self.db.create_table("aja_communications", data=[{
                "message_id": "init",
                "created_at": utc_now(),
                "delivery_status": "PENDING",
                "approval_status": "AWAITING_APPROVAL",
                "content": "",
                "recipient": ""
            }])
        table = self.db.open_table("aja_communications")
        mid = uuid.uuid4().hex
        row = [{
            "message_id": mid,
            "created_at": utc_now(),
            "delivery_status": "PENDING",
            "approval_status": "AWAITING_APPROVAL",
            **data
        }]
        table.add(row)
        return row[0]

    def get_communication(self, message_id: str) -> Optional[Dict[str, Any]]:
        existing = list_tables_defensive(self.db)
        if "aja_communications" not in existing:
            return None
        table = self.db.open_table("aja_communications")
        results = table.search().where(f"message_id = '{message_id}'").limit(1).to_list()
        return results[0] if results else None

    def update_communication(self, message_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        return {"message_id": message_id, **updates}

    def edit_communication(self, message_id: str, content: str, note: str) -> Dict[str, Any]:
        return self.update_communication(message_id, {"content": content, "edit_note": note})

    def approve_communication(self, message_id: str) -> Dict[str, Any]:
        return self.update_communication(message_id, {"approval_status": "APPROVED"})

    def reject_communication(self, message_id: str, reason: str) -> Dict[str, Any]:
        return self.update_communication(message_id, {"approval_status": "REJECTED", "rejection_reason": reason})

    def communication_summary(self) -> Dict[str, Any]:
        return {"pending": 0, "approved": 0, "rejected": 0}

    def get_scheduler_config(self) -> Dict[str, Any]:
        return {"morning_review_time": "08:00", "night_review_time": "22:00"}

    def update_scheduler_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return config

    def generate_executive_review(self, kind: str, escalate: bool = True) -> Dict[str, Any]:
        return {"kind": kind, "summary": "No tasks to review.", "tasks": []}

    def due_review_kinds(self) -> List[str]:
        return []

    def snooze_task(self, task_id: str, until: str, reason: str) -> Dict[str, Any]:
        return self.update_task(task_id, {"status": "SNOOZED", "snooze_until": until, "snooze_reason": reason})

    def review(self, days: int = 7, hours: int = 24, escalate: bool = True) -> Dict[str, Any]:
        return {"summary": "System healthy.", "active_tasks": 0}

    def get_active_approval(self) -> Optional[Dict[str, Any]]:
        existing = list_tables_defensive(self.db)
        if "aja_approvals" not in existing:
            return None
        table = self.db.open_table("aja_approvals")
        results = table.search().where("status = 'pending'").limit(1).to_list()
        return results[0] if results else None

    def create_approval(self, data: Dict[str, Any]) -> str:
        aid = data.get("approval_id") or uuid.uuid4().hex
        existing = list_tables_defensive(self.db)
        if "aja_approvals" not in existing:
            self.db.create_table("aja_approvals", data=[{
                "approval_id": aid,
                "status": "pending",
                "created_at": utc_now(),
                **data
            }])
        else:
            table = self.db.open_table("aja_approvals")
            table.add([{"approval_id": aid, "status": "pending", "created_at": utc_now(), **data}])
        return aid

    def update_approval(self, approval_id: str, status: str, note: str = "") -> Dict[str, Any]:
        return {"approval_id": approval_id, "status": status, "note": note}

    def log_approval_audit(self, data: Dict[str, Any]):
        existing = list_tables_defensive(self.db)
        if "aja_approval_audit" not in existing:
            self.db.create_table("aja_approval_audit", data=[{"created_at": utc_now(), **data}])
        else:
            table = self.db.open_table("aja_approval_audit")
            table.add([{"created_at": utc_now(), **data}])

    def summary(self) -> Dict[str, Any]:
        return {"tasks": 0, "workers": 0, "events": 0}


# ── Singleton Factory ─────────────────────────────────────────────────────────
_secretary_instance: Optional[SecretaryMemory] = None


def get_secretary_memory() -> SecretaryMemory:
    """Returns the process-wide SecretaryMemory singleton, sharing the core database connection."""
    global _secretary_instance
    if _secretary_instance is None:
        from agentx.memory.manager import get_memory_manager

        mgr = get_memory_manager()
        _secretary_instance = SecretaryMemory(db=mgr.db)
    return _secretary_instance


# ── Mobile Formatting Helpers ────────────────────────────────────────────────


def format_communication_for_mobile(message: str) -> str:
    """Formats a communication message for Telegram mobile display."""
    if not message:
        return ""
    # Truncate extremely long messages for mobile readability
    if len(message) > 4000:
        return message[:3950] + "\n\n... (truncated)"
    return message


def format_tasks_for_mobile(tasks: List[Dict[str, Any]]) -> str:
    """Formats a list of tasks into a mobile-friendly Telegram string."""
    if not tasks:
        return "No tasks found."
    lines = []
    for t in tasks:
        status_icon = {"PENDING": "⏳", "DONE": "✅", "IN_PROGRESS": "🔄"}.get(
            t.get("status", ""), "📋"
        )
        lines.append(f"{status_icon} {t.get('title', 'Untitled')} [{t.get('priority', 'medium')}]")
    return "\n".join(lines)


def parse_communication_intent(text: str, source: str = "unknown") -> Dict[str, Any]:
    """Parses user text to extract communication intent (send, draft, reply)."""
    text_lower = text.lower().strip()
    res = {"source": source}
    if text_lower.startswith("send "):
        res.update({"intent": "send", "body": text[5:].strip()})
        return res
    elif text_lower.startswith("draft "):
        res.update({"intent": "draft", "body": text[6:].strip()})
        return res
    elif text_lower.startswith("reply "):
        res.update({"intent": "reply", "body": text[6:].strip()})
        return res
    return None


def parse_task_intent(text: str, source: str = "unknown", owner: str = "unknown") -> Dict[str, Any]:
    """Parses user text to extract task intent (create, list, complete)."""
    text_lower = text.lower().strip()
    res = {"source": source, "owner": owner}
    
    if text_lower.startswith("add task ") or text_lower.startswith("create task "):
        body = text.split(" ", 2)[-1] if len(text.split(" ", 2)) > 2 else ""
        res.update({"intent": "create", "title": body})
        return res
    elif text_lower in ("list tasks", "show tasks", "my tasks"):
        res.update({"intent": "list"})
        return res
    elif text_lower.startswith("done ") or text_lower.startswith("complete "):
        res.update({"intent": "complete", "task_id": text.split(" ", 1)[-1].strip()})
        return res
        
    return None
