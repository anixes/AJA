import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict
from agentx.memory.manager import MemoryManager, get_memory_manager

_manager = get_memory_manager()

def add_trigger(trigger_type: str, condition_payload: dict, action_payload: dict, cooldown_seconds: int = 60) -> str:
    table = _manager.get_table("core_triggers")
    tid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    row = [{
        "trigger_id": tid,
        "event_pattern": trigger_type, # Simplified for POC
        "action_json": json.dumps(action_payload),
        "status": "ACTIVE",
        "created_at": now
    }]
    table.add(row)
    return tid

def fetch_active_triggers() -> List[Dict]:
    table = _manager.get_table("core_triggers")
    # LanceDB query via Arrow
    results = table.search().where("status = 'ACTIVE'").to_list()
    return results

def disable_trigger(trigger_id: str):
    table = _manager.get_table("core_triggers")
    table.update(where=f"trigger_id = '{trigger_id}'", values={"status": "DISABLED"})

def delete_trigger(trigger_id: str):
    # Columnar deletion usually via filtering or compaction
    pass
