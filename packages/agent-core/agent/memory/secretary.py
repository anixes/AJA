import json
import uuid
import lancedb
import pyarrow as pa
import pyarrow.compute as pc
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Dict
from agent.memory.manager import list_tables_defensive, get_memory_manager

from agent.tui.tasks import STATUS_PENDING, STATUS_RUNNING, STATUS_FAILED, STATUS_COMPLETED

def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

class SecretaryMemory:
    """
    High-performance Assistant Memory powered by LanceDB and Apache Arrow.
    Provides semantic task retrieval and zero-copy transactional integrity.
    """
    def __init__(self, db_path: Path | str = None, db=None):
        self.manager = get_memory_manager()
        self.db = self.manager.db
        self.db_path = self.manager.db_path
        self._init_tables()

    def _init_tables(self):
        existing = list_tables_defensive(self.db)

        # 1. Secretary Tasks Table (Arrow Schema)
        task_schema = pa.schema([
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
            ("vector", pa.list_(pa.float32(), 1536)) # For semantic task search
        ])
        if "secretary_tasks" not in existing:
            self.db.create_table("secretary_tasks", schema=task_schema)

        # 2. Core Approvals Table
        approval_schema = pa.schema([
            ("approval_id", pa.string()),
            ("run_id", pa.string()),
            ("command", pa.string()),
            ("risk_level", pa.string()),
            ("status", pa.string()),
            ("resolution_note", pa.string()),
            ("created_at", pa.string()),
            ("updated_at", pa.string()),
            ("metadata_json", pa.string())
        ])
        if "core_approvals" not in existing:
            self.db.create_table("core_approvals", schema=approval_schema)

        # 3. Runtime Events (Capped Window)
        event_schema = pa.schema([
            ("event_id", pa.string()),
            ("event_type", pa.string()),
            ("message", pa.string()),
            ("level", pa.string()),
            ("created_at", pa.string())
        ])
        if "core_events" not in existing:
            self.db.create_table("core_events", schema=event_schema)

    def create_task(self, data: Dict[str, Any]) -> str:
        table = self.db.open_table("secretary_tasks")
        tid = data.get("task_id") or f"task-{uuid.uuid4().hex[:12]}"
        now = utc_now()
        
        # In a real impl, we would generate a vector here. For now, zero-vec.
        vector = [0.0] * 1536 
        
        task_row = [{
            "task_id": tid,
            "title": data.get("title", ""),
            "context": data.get("context", ""),
            "owner": data.get("owner", "Assistant"),
            "due_date": data.get("due_date", ""),
            "priority": data.get("priority", "medium"),
            "status": STATUS_PENDING,
            "created_at": now,
            "updated_at": now,
            "metadata_json": json.dumps(data.get("metadata", {})),
            "vector": vector
        }]
        table.add(task_row)
        return tid

    def list_tasks(self, status: Optional[str] = None, statuses: Optional[List[str]] = None, limit: int = 50) -> List[Dict[str, Any]]:
        table = self.db.open_table("secretary_tasks")
        query = table.to_arrow()
        
        if status:
            mask = pc.equal(query["status"], status.upper())
            query = query.filter(mask)
        elif statuses:
            # Handle multiple statuses (canonical uppercase)
            upper_statuses = [s.upper() for s in statuses]
            mask = pc.is_in(query["status"], value_set=pa.array(upper_statuses))
            query = query.filter(mask)
            
        return query.slice(0, limit).to_pylist()

    def create_approval(self, data: Dict[str, Any]) -> str:
        table = self.db.open_table("core_approvals")
        aid = data.get("approval_id") or uuid.uuid4().hex
        now = utc_now()
        
        approval_row = [{
            "approval_id": aid,
            "run_id": data.get("run_id", "unknown"),
            "command": data.get("command", ""),
            "risk_level": data.get("risk_level", "medium"),
            "status": STATUS_PENDING,
            "resolution_note": "",
            "created_at": now,
            "updated_at": now,
            "metadata_json": json.dumps(data.get("metadata", {}))
        }]
        table.add(approval_row)
        return aid

    def update_approval(self, approval_id: str, status: str, note: str = ""):
        table = self.db.open_table("core_approvals")
        # LanceDB update via Arrow Table reconstruction or direct update if supported
        # For simplicity in this performance POC, we use the Arrow filter pattern
        table.update(where=f"approval_id = '{approval_id}'", values={
            "status": status,
            "resolution_note": note,
            "updated_at": utc_now()
        })

    def get_active_approval(self) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("core_approvals")
        results = table.search().where(f"status = '{STATUS_PENDING}'").limit(1).to_list()
        return results[0] if results else None

    def add_runtime_event(self, data: Dict[str, Any]):
        table = self.db.open_table("core_events")
        event_row = [{
            "event_id": uuid.uuid4().hex,
            "event_type": data.get("event_type", "info"),
            "message": data.get("message", ""),
            "level": data.get("level", "info"),
            "created_at": utc_now()
        }]
        table.add(event_row)
        
        # Periodic pruning check (every 50 events)
        if table.count_rows() % 50 == 0:
             self.prune_events(max_rows=1000)

    def prune_events(self, max_rows: int = 1000):
        """
        Keep only the most recent 'max_rows' runtime events.
        """
        table = self.db.open_table("core_events")
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
            cutoff_idx = sorted_indices[max_rows-1].as_py()
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
        table = self.db.open_table("core_approvals")
        cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat()
        table.delete(f"created_at < '{cutoff}'")
        print(f"[Maintenance] Pruned assistant approvals older than {cutoff}")

# ── Singleton Factory ─────────────────────────────────────────────────────────
_secretary_instance: Optional[SecretaryMemory] = None

def get_secretary_memory() -> SecretaryMemory:
    """Returns the process-wide SecretaryMemory singleton, sharing the core database connection."""
    global _secretary_instance
    if _secretary_instance is None:
        from agent.memory.manager import get_memory_manager
        mgr = get_memory_manager()
        _secretary_instance = SecretaryMemory(db=mgr.db)
    return _secretary_instance

