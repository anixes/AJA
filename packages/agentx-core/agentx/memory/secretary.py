import uuid
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import lancedb
from agentx.config import PROJECT_ROOT

logger = logging.getLogger("agentx.memory.secretary")

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def list_tables_defensive(db) -> List[str]:
    try:
        return db.table_names()
    except Exception:
        return []

class AJAMemory:
    """
    AJA Memory (Assistant of Joint Agents).
    Handles structured task persistence, obligations, and executive accountability.
    Utilizes LanceDB/Arrow for high-speed, zero-copy storage.
    """

    def __init__(self, db_path: str = "./.agentx/lancedb"):
        self.db = lancedb.connect(db_path)
        self._init_tables()

    def _init_tables(self):
        existing = list_tables_defensive(self.db)
        
        # 1. Tasks Table (Core obligations)
        if "aja_tasks" not in existing:
            self.db.create_table("aja_tasks", data=[{
                "task_id": "init",
                "title": "System Check",
                "context": "AJA Memory Initialized",
                "owner": "system",
                "due_date": utc_now(),
                "priority": "low",
                "status": "archived",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "completion_note": "",
                "metadata_json": "{}",
                "vector": [0.0] * 1536
            }])

        # 2. Communications Table
        if "aja_communications" not in existing:
            self.db.create_table("aja_communications", data=[{
                "message_id": "init",
                "recipient": "none",
                "content": "",
                "draft_content": "",
                "channel": "system",
                "delivery_status": "none",
                "approval_status": "none",
                "created_at": utc_now(),
                "updated_at": utc_now()
            }])

        # 3. Approvals Table
        if "aja_approvals" not in existing:
            self.db.create_table("aja_approvals", data=[{
                "approval_id": "init",
                "kind": "system",
                "description": "init",
                "status": "approved",
                "resolution_note": "",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "metadata_json": "{}",
                "vector": [0.0] * 1536
            }])

        # 4. Workers/Swarm State
        if "aja_workers" not in existing:
            self.db.create_table("aja_workers", data=[{
                "worker_id": "init",
                "name": "System",
                "availability_status": "active",
                "created_at": utc_now()
            }])

        # 5. Runtime Events
        if "aja_runtime_events" not in existing:
            self.db.create_table("aja_runtime_events", data=[{
                "event_id": "init",
                "kind": "init",
                "target": "none",
                "status": "none",
                "message": "System Init",
                "command": "",
                "metadata_json": "{}",
                "timestamp": utc_now()
            }])

        # 6. Territory Knowledge (RAG)
        if "aja_territory_knowledge" not in existing:
            self.db.create_table("aja_territory_knowledge", data=[{
                "id": "seed",
                "path": "init",
                "content": "Territory initialization.",
                "metadata_json": "{}",
                "updated_at": utc_now(),
                "vector": [0.0] * 1536
            }])

    # --- Task Management ---

    def create_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
        tid = data.get("task_id") or uuid.uuid4().hex[:8]
        table = self.db.open_table("aja_tasks")
        row = {
            "task_id": tid,
            "title": data.get("title", "Untitled Task"),
            "context": data.get("context", ""),
            "owner": data.get("owner", "unknown"),
            "priority": data.get("priority", "medium"),
            "status": data.get("status", "pending"),
            "due_date": data.get("due_date"),
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "completion_note": "",
            "metadata_json": json.dumps(data.get("metadata", {})),
            "vector": [0.0] * 1536
        }
        table.add([row])
        return row

    def list_tasks(self, status: Optional[str] = None, statuses: List[str] | None = None, limit: int = 50) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_tasks")
        query = table.search()
        if status:
            query = query.where(f"status = '{status}'")
        elif statuses:
            s_list = ", ".join(f"'{s}'" for s in statuses)
            query = query.where(f"status IN ({s_list})")
        return query.limit(limit).to_list()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_tasks")
        results = table.search().where(f"task_id = '{task_id}'").limit(1).to_list()
        return results[0] if results else None

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        table = self.db.open_table("aja_tasks")
        updates["updated_at"] = utc_now()
        table.update(where=f"task_id = '{task_id}'", values=updates)
        return self.get_task(task_id)

    def complete_task(self, task_id: str, note: str = "") -> Dict[str, Any]:
        return self.update_task(task_id, {"status": "completed", "completion_note": note})

    def archive_task(self, task_id: str, note: str = "") -> Dict[str, Any]:
        return self.update_task(task_id, {"status": "archived", "completion_note": note})

    def snooze_task(self, task_id: str, until: str, reason: str = "") -> Dict[str, Any]:
        return self.update_task(task_id, {
            "status": "snoozed",
            "due_date": until,
            "completion_note": reason
        })

    # --- Worker/Swarm Management ---

    def list_workers(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_workers")
        query = table.search()
        if status:
            query = query.where(f"availability_status = '{status}'")
        return query.limit(limit).to_list()

    def create_worker(self, data: Dict[str, Any]) -> Dict[str, Any]:
        table = self.db.open_table("aja_workers")
        wid = data.get("worker_id") or uuid.uuid4().hex[:8]
        row = {
            "worker_id": wid, 
            "name": data.get("name", "Unknown"),
            "availability_status": "active",
            "created_at": utc_now(),
            **data
        }
        table.add([row])
        return row

    # --- Communication Management ---

    def create_communication(self, data: Dict[str, Any]) -> Dict[str, Any]:
        table = self.db.open_table("aja_communications")
        mid = uuid.uuid4().hex[:8]
        row = {
            "message_id": mid,
            "recipient": data.get("recipient", "unknown"),
            "content": data.get("content", ""),
            "draft_content": data.get("draft_content", ""),
            "channel": data.get("channel", "telegram"),
            "delivery_status": "pending",
            "approval_status": "awaiting",
            "created_at": utc_now(),
            "updated_at": utc_now()
        }
        table.add([row])
        return row

    def list_communications(self, approval_status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_communications")
        query = table.search()
        if approval_status:
            query = query.where(f"approval_status = '{approval_status}'")
        return query.limit(limit).to_list()

    def get_communication(self, message_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_communications")
        results = table.search().where(f"message_id = '{message_id}'").limit(1).to_list()
        return results[0] if results else None

    def get_communication_history(self, recipient: str, limit: int = 5) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_communications")
        return table.search().where(f"recipient = '{recipient}'").limit(limit).to_list()

    def approve_communication(self, message_id: str) -> Dict[str, Any]:
        return self.update_communication(message_id, {"approval_status": "approved"})

    def reject_communication(self, message_id: str, reason: str = "") -> Dict[str, Any]:
        return self.update_communication(message_id, {"approval_status": "rejected", "rejection_reason": reason})

    def update_communication(self, message_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        table = self.db.open_table("aja_communications")
        updates["updated_at"] = utc_now()
        table.update(where=f"message_id = '{message_id}'", values=updates)
        return self.get_communication(message_id)

    def mark_communication_sent(self, message_id: str, note: str = "") -> Dict[str, Any]:
        return self.update_communication(message_id, {"delivery_status": "sent", "content": note})

    def list_communications(self, statuses: List[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_communications")
        query = table.search()
        if statuses:
            status_filter = " OR ".join([f"approval_status = '{s}'" for s in statuses])
            query = query.where(f"({status_filter})")
        return query.limit(limit).to_list()

    # --- Approval Management ---

    def create_approval(self, data: Dict[str, Any]) -> str:
        aid = data.get("approval_id") or uuid.uuid4().hex[:8]
        table = self.db.open_table("aja_approvals")
        row = {
            "approval_id": aid,
            "kind": data.get("kind", "manual"),
            "description": data.get("description", ""),
            "status": "pending",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "metadata_json": json.dumps(data.get("metadata", {})),
            "vector": [0.0] * 1536
        }
        table.add([row])
        return aid

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_approvals")
        results = table.search().where(f"approval_id = '{approval_id}'").limit(1).to_list()
        return results[0] if results else None

    def get_active_approval(self) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_approvals")
        results = table.search().where("status = 'pending'").limit(1).to_list()
        return results[0] if results else None

    def update_approval(self, approval_id: str, status: str, note: str = "") -> Dict[str, Any]:
        table = self.db.open_table("aja_approvals")
        table.update(where=f"approval_id = '{approval_id}'", values={"status": status, "resolution_note": note, "updated_at": utc_now()})
        return self.get_approval(approval_id)

    def list_approvals(self, statuses: List[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_approvals")
        query = table.search()
        if statuses:
            status_filter = " OR ".join([f"status = '{s}'" for s in statuses])
            query = query.where(f"({status_filter})")
        return query.limit(limit).to_list()

    def log_approval_audit(self, entry: Dict[str, Any]):
        self.record_scheduler_event("approval_audit", entry.get("approval_id", "none"), entry)

    # --- Runtime Events ---

    def add_runtime_event(self, event: Dict[str, Any]) -> str:
        return self.record_scheduler_event(
            kind=event.get("event_type", "INFO"),
            target=event.get("tool", "system"),
            metadata=event,
            status=True
        )

    def record_scheduler_event(self, kind: str, target: str, metadata: Dict[str, Any], status: bool = True) -> str:
        eid = uuid.uuid4().hex[:8]
        table = self.db.open_table("aja_runtime_events")
        row = {
            "event_id": eid,
            "kind": kind,
            "target": target,
            "status": "success" if status else "failed",
            "metadata_json": json.dumps(metadata),
            "timestamp": utc_now()
        }
        table.add([row])
        return eid

    # --- Maintenance ---

    def cleanup_old_tasks(self, ttl_days: int = 30):
        table = self.db.open_table("aja_tasks")
        # In LanceDB, we usually overwrite or filter. Deletion is sometimes limited.
        # For simplicity in this mock-like wrapper, we'll just log it.
        # Real LanceDB: table.delete("updated_at < ...")
        pass

    def cleanup_old_approvals(self, ttl_days: int = 30):
        pass

    def prune_events(self, max_rows: int = 1000):
        pass

    # --- RAG & Territory Knowledge ---

    def add_knowledge_chunk(self, path: str, content: str, metadata: Dict[str, Any], vector: List[float]):
        table = self.db.open_table("aja_territory_knowledge")
        row = {
            "id": uuid.uuid4().hex[:8],
            "path": path,
            "content": content,
            "metadata_json": json.dumps(metadata),
            "updated_at": utc_now(),
            "vector": vector
        }
        table.add([row])

    def query_territory(self, query_vector: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_territory_knowledge")
        return table.search(query_vector).limit(limit).to_list()

    def clear_territory_knowledge(self, path_prefix: str = ""):
        table = self.db.open_table("aja_territory_knowledge")
        if path_prefix:
            # LanceDB deletion is a bit complex, we'll use filter if supported
            # table.delete(f"path LIKE '{path_prefix}%'")
            pass
        else:
            # table.delete("TRUE")
            pass

    # --- Summaries ---

    def summary(self) -> Dict[str, Any]:
        existing = list_tables_defensive(self.db)
        counts = {}
        target_tables = [
            "aja_tasks", "aja_approvals", "aja_workers", 
            "aja_communications", "aja_territory_knowledge", "aja_skills"
        ]
        for tbl in target_tables:
            if tbl in existing:
                try:
                    counts[tbl] = self.db.open_table(tbl).count_rows()
                except:
                    counts[tbl] = 0
            else:
                counts[tbl] = 0
        return counts

    def review(self, escalate: bool = False) -> str:
        stats = self.summary()
        return f"AJA System Review: {stats.get('aja_tasks', 0)} tasks, {stats.get('aja_workers', 0)} workers active."

    def generate_executive_review(self, kind: str = "morning", escalate: bool = False) -> Dict[str, Any]:
        tasks = self.list_tasks(statuses=["pending", "active"], limit=5)
        return {
            "kind": kind,
            "timestamp": utc_now(),
            "active_tasks": len(tasks),
            "summary": f"AJA {kind.capitalize()} Review: {len(tasks)} tasks requiring attention."
        }

# ── Standalone Helpers ────────────────────────────────────────────────────────

def format_tasks_for_mobile(tasks: List[Dict], review: str = "") -> str:
    lines = []
    if review:
        lines.append(f"💡 {review}")
        lines.append("")
    if not tasks:
        lines.append("No active tasks.")
    else:
        for t in tasks:
            status_icon = "🟢" if t["status"] == "completed" else "⏳" if t["status"] == "active" else "⚪"
            lines.append(f"{status_icon} [{t['task_id'][:4]}] {t['title']}")
            if t.get("due_date"):
                lines.append(f"   Due: {t['due_date']}")
            lines.append("")
    return "\n".join(lines).strip()

def format_communication_for_mobile(comm: Dict) -> str:
    status = comm.get("approval_status", "pending")
    icon = "✅" if status == "approved" else "❌" if status == "rejected" else "❓"
    return f"{icon} Message for: {comm['recipient']}\n---\n{comm.get('content') or comm.get('draft_content')}\n---\nStatus: {status}"

def parse_communication_intent(text: str, source: str = "unknown") -> Optional[Dict]:
    lowered = text.lower()
    if any(k in lowered for k in ["tell ", "message ", "ask ", "send message to "]):
        return {"recipient": "TBD", "content": text, "source": source}
    return None

def parse_task_intent(text: str, source: str = "unknown", owner: str = "unknown") -> Optional[Dict]:
    lowered = text.lower()
    task_triggers = ["todo:", "task:", "remind me to", "don't forget to", "remind me:", "need to", "i should"]
    if any(lowered.startswith(t) for t in task_triggers) or (len(text.split()) > 3 and any(k in lowered for k in ["todo", "task", "remind"])):
        return {"title": text, "priority": "medium", "source": source, "owner": owner}
    return None

# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[AJAMemory] = None

def get_aja_memory() -> AJAMemory:
    global _instance
    if _instance is None:
        db_path = f"{PROJECT_ROOT}/.agentx/lancedb"
        _instance = AJAMemory(db_path)
    return _instance
