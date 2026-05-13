import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict
from agent.memory.manager import MemoryManager, get_memory_manager

from agent.tui.tasks import STATUS_ACTIVE, STATUS_DISABLED

_manager = get_memory_manager()

def add_trigger(trigger_type: str, condition_payload: dict, action_payload: dict, cooldown_seconds: int = 60) -> str:
    table = _manager.get_table("core_triggers")
    tid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    row = [{
        "trigger_id": tid,
        "event_pattern": trigger_type, # Simplified for POC
        "action_json": json.dumps(action_payload),
        "status": STATUS_ACTIVE,
        "created_at": now
    }]
    table.add(row)
    return tid

def fetch_active_triggers() -> List[Dict]:
    table = _manager.get_table("core_triggers")
    # LanceDB query via Arrow
    results = table.search().where(f"status = '{STATUS_ACTIVE}'").to_list()
    return results

def disable_trigger(trigger_id: str):
    table = _manager.get_table("core_triggers")
    table.update(where=f"trigger_id = '{trigger_id}'", values={"status": STATUS_DISABLED})

def delete_trigger(trigger_id: str):
    # Columnar deletion usually via filtering or compaction
    pass
