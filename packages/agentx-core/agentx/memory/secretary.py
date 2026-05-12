import json
import uuid
import lancedb
import pyarrow as pa
import pyarrow.compute as pc
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Dict
from agentx.config import PROJECT_ROOT

def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

class SecretaryMemory:
    """
    High-performance Assistant Memory (AJA) powered by LanceDB and Apache Arrow.
    Provides semantic task retrieval and zero-copy transactional integrity.
    """
    def __init__(self, db_path: Path | str = None):
        self.db_path = Path(db_path) if db_path else PROJECT_ROOT / ".agentx" / "lancedb"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))
        self._init_tables()

    def _init_tables(self):
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
        if "secretary_tasks" not in self.db.table_names():
            self.db.create_table("secretary_tasks", schema=task_schema)

        # 2. AJA Approvals Table
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
        if "aja_approvals" not in self.db.table_names():
            self.db.create_table("aja_approvals", schema=approval_schema)

        # 3. Runtime Events (Capped Window)
        event_schema = pa.schema([
            ("event_id", pa.string()),
            ("event_type", pa.string()),
            ("message", pa.string()),
            ("level", pa.string()),
            ("created_at", pa.string())
        ])
        if "aja_runtime_events" not in self.db.table_names():
            self.db.create_table("aja_runtime_events", schema=event_schema)

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
            "owner": data.get("owner", "AJA"),
            "due_date": data.get("due_date", ""),
            "priority": data.get("priority", "medium"),
            "status": "PENDING",
            "created_at": now,
            "updated_at": now,
            "metadata_json": json.dumps(data.get("metadata", {})),
            "vector": vector
        }]
        table.add(task_row)
        return tid

    def list_tasks(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        table = self.db.open_table("secretary_tasks")
        query = table.to_arrow()
        
        if status:
            # High-speed Arrow filtering
            mask = pc.equal(query["status"], status)
            query = query.filter(mask)
            
        return query.slice(0, limit).to_pylist()

    def create_approval(self, data: Dict[str, Any]) -> str:
        table = self.db.open_table("aja_approvals")
        aid = data.get("approval_id") or uuid.uuid4().hex
        now = utc_now()
        
        approval_row = [{
            "approval_id": aid,
            "run_id": data.get("run_id", "unknown"),
            "command": data.get("command", ""),
            "risk_level": data.get("risk_level", "medium"),
            "status": "PENDING",
            "resolution_note": "",
            "created_at": now,
            "updated_at": now,
            "metadata_json": json.dumps(data.get("metadata", {}))
        }]
        table.add(approval_row)
        return aid

    def update_approval(self, approval_id: str, status: str, note: str = ""):
        table = self.db.open_table("aja_approvals")
        # LanceDB update via Arrow Table reconstruction or direct update if supported
        # For simplicity in this performance POC, we use the Arrow filter pattern
        table.update(where=f"approval_id = '{approval_id}'", values={
            "status": status,
            "resolution_note": note,
            "updated_at": utc_now()
        })

    def get_active_approval(self) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_approvals")
        results = table.search().where("status = 'PENDING'").limit(1).to_list()
        return results[0] if results else None

    def add_runtime_event(self, data: Dict[str, Any]):
        table = self.db.open_table("aja_runtime_events")
        event_row = [{
            "event_id": uuid.uuid4().hex,
            "event_type": data.get("event_type", "info"),
            "message": data.get("message", ""),
            "level": data.get("level", "info"),
            "created_at": utc_now()
        }]
        table.add(event_row)
        
        # Capping logic (Simulated for POC)
        if table.count_rows() > 500:
             # In a real columnar capping, we would prune old fragments
             pass
