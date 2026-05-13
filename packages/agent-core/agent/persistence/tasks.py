import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from agent.memory.manager import MemoryManager, get_memory_manager

from agent.tui.tasks import STATUS_PENDING, STATUS_RUNNING, STATUS_FAILED, STATUS_COMPLETED

# Singleton manager
_manager = get_memory_manager()

def init_db():
    # Tables are initialized in MemoryManager.__init__
    pass

def create_task(payload: dict) -> str:
    table = _manager.get_table("core_tasks")
    tid = f"task-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    
    task_row = [{
        "task_id": tid,
        "run_id": str(uuid.uuid4()),
        "objective": json.dumps(payload),
        "status": STATUS_PENDING,
        "retry_count": 0,
        "created_at": now,
        "updated_at": now,
        "metadata_json": "{}"
    }]
    table.add(task_row)
    print(f"[Tasks][{tid}] Created task: {STATUS_PENDING}")
    return tid

def update_task_status(task_id: str, status: str):
    table = _manager.get_table("core_tasks")
    table.update(where=f"task_id = '{task_id}'", values={
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })
    print(f"[Tasks][{task_id}] Transitioned to {status}")

def fetch_pending_tasks(limit: int = 10) -> List[Dict]:
    table = _manager.get_table("core_tasks")
    # LanceDB query via Arrow
    results = table.search().where(f"status IN ('INTERRUPTED', '{STATUS_PENDING}', '{STATUS_FAILED}')").limit(limit).to_list()
    # Sort in memory for correct prioritization (INTERRUPTED > PENDING > FAILED)
    priority_map = {"INTERRUPTED": 1, STATUS_PENDING: 2, STATUS_FAILED: 3}
    results.sort(key=lambda x: (priority_map.get(x["status"], 9), x["updated_at"]))
    return results

def is_logical_task_completed(logical_task_id: str) -> bool:
    # In Pure Agent, we search metadata for logical links
    table = _manager.get_table("core_tasks")
    results = table.search().where(f"status = '{STATUS_COMPLETED}' AND task_id LIKE '%{logical_task_id}%'").limit(1).to_list()
    return len(results) > 0

def cleanup_old_tasks(ttl_days: int = 30):
    """
    Prune tasks older than ttl_days to maintain database performance.
    """
    table = _manager.get_table("core_tasks")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    table.delete(f"created_at < '{cutoff}'")
    print(f"[Maintenance] Pruned tasks older than {cutoff}")
