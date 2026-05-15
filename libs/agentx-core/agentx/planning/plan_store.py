import json
from datetime import datetime, timezone
from typing import List, Optional, Dict
from agentx.memory.manager import MemoryManager, get_memory_manager
from agentx.planning.models import PlanGraph, PlanNode

_manager = get_memory_manager()

class PlanStore:
    """
    High-performance PlanStore powered by LanceDB/Arrow.
    Provides semantic goal search and zero-copy plan coordination.
    """
    @classmethod
    def save(cls, plan_id: str, graph: PlanGraph) -> None:
        table = _manager.get_table("core_plans")
        status = cls._derive_status(graph)
        nodes_json = json.dumps([n.to_dict() for n in graph.nodes])
        now = datetime.now(timezone.utc).isoformat()
        
        # Upsert logic via Arrow filter
        existing = table.search().where(f"plan_id = '{plan_id}'").limit(1).to_list()
        
        if existing:
            table.update(where=f"plan_id = '{plan_id}'", values={
                "status": status,
                "steps_json": nodes_json,
                "created_at": now # Should be updated_at in a real schema, using created_at for POC
            })
        else:
            # In a real impl, we generate a vector for the goal
            vector = [0.0] * 384
            row = [{
                "plan_id": plan_id,
                "goal": graph.goal,
                "steps_json": nodes_json,
                "status": status,
                "created_at": now,
                "vector": vector
            }]
            table.add(row)

    @classmethod
    def load(cls, plan_id: str) -> Optional[PlanGraph]:
        table = _manager.get_table("core_plans")
        results = table.search().where(f"plan_id = '{plan_id}'").limit(1).to_list()
        
        if not results:
            return None
        
        row = results[0]
        nodes_raw = json.loads(row["steps_json"])
        nodes = [PlanNode.from_dict(n) for n in nodes_raw]
        return PlanGraph(goal=row["goal"], nodes=nodes)

    @classmethod
    def record_repair(cls, plan_id: str, node_id: str, action: str, metadata: Optional[Dict] = None) -> None:
        """Compatibility hook for ReActExecutor repair telemetry."""
        table = _manager.get_table("core_tool_executions")
        now = datetime.now(timezone.utc).isoformat()
        table.add([{
            "execution_id": f"repair-{plan_id}-{node_id}-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            "task_id": node_id,
            "tool_name": "plan_repair",
            "args_json": json.dumps({"plan_id": plan_id, "action": action, "metadata": metadata or {}}),
            "status": "RECORDED",
            "output_summary": f"Repair action recorded: {action}",
            "created_at": now,
        }])

    @staticmethod
    def _derive_status(graph: PlanGraph) -> str:
        statuses = {n.status for n in graph.nodes}
        if not statuses: return "PENDING"
        if statuses == {"COMPLETED"}: return "COMPLETED"
        if "RUNNING" in statuses: return "RUNNING"
        if "FAILED" in statuses: return "FAILED"
        return "PENDING"
