import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict
from agent.memory.manager import MemoryManager, get_memory_manager

_manager = get_memory_manager()

class ToolGuard:
    """
    High-performance ToolGuard powered by LanceDB/Arrow.
    Ensures idempotency and provides zero-copy result caching.
    """
    def __init__(self, run_id: str, tool_name: str, args: dict, step: str = "main"):
        self.tool_name = tool_name
        self.step = step
        args_hash = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        self.args_hash = args_hash
        self.idempotency_key = f"{run_id}:{tool_name}:{step}:{args_hash}"

    def reserve(self) -> Optional[Dict]:
        table = _manager.get_table("core_tool_executions")
        now = datetime.now(timezone.utc).isoformat()
        
        # Check for existing execution
        existing = table.search().where(f"execution_id = '{self.idempotency_key}'").limit(1).to_list()
        
        if existing:
            res = existing[0]
            if res["status"] == "COMPLETED":
                print(f"[ToolGuard][OK] Coalescing {self.tool_name}:{self.step} -- returning cached result.")
                return {"result": res["output_summary"]}
            return {"status": res["status"]}

        # Create new execution record
        row = [{
            "execution_id": self.idempotency_key,
            "task_id": "unknown", # task_id would be set by the orchestrator
            "tool_name": self.tool_name,
            "args_json": self.args_hash,
            "status": "RUNNING",
            "output_summary": "",
            "created_at": now
        }]
        table.add(row)
        return None

    def complete(self, result: str):
        table = _manager.get_table("core_tool_executions")
        table.update(where=f"execution_id = '{self.idempotency_key}'", values={
            "status": "COMPLETED",
            "output_summary": str(result)
        })
        print(f"[ToolGuard][{self.idempotency_key}] Completed successfully.")

    def fail(self, error: str, error_type: str = "RETRYABLE"):
        status = f"FAILED_{error_type}"
        table = _manager.get_table("core_tool_executions")
        table.update(where=f"execution_id = '{self.idempotency_key}'", values={
            "status": status,
            "output_summary": error
        })
        print(f"[ToolGuard][{self.idempotency_key}] Failed ({error_type}): {error}")

def cleanup_old_entries(ttl_days: int = 30):
    """
    Prune tool execution logs older than ttl_days to manage database size.
    """
    table = _manager.get_table("core_tool_executions")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    table.delete(f"created_at < '{cutoff}'")
    print(f"[Maintenance] Pruned tool executions older than {cutoff}")

class PermanentError(Exception): pass
class RetryableError(Exception): pass
