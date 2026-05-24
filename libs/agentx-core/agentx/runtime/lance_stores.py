"""Lance-backed compatibility adapters for runtime persistence contracts."""

from typing import Any, Dict, List, Optional

LEGACY_AJA_TABLE_PREFIX = "aja_"


class LanceRuntimeStore:
    """Adapter around the current Lance memory implementation."""

    def __init__(self, memory: Any = None):
        if memory is None:
            from agentx.memory.secretary import get_aja_memory

            memory = get_aja_memory()
        self.memory = memory

    def get_active_approval(self) -> Optional[Dict[str, Any]]:
        return self.memory.get_active_approval()

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        return self.memory.get_approval(approval_id)

    def create_approval(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self.memory.create_approval(data)

    def update_approval(self, approval_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        return self.memory.update_approval(approval_id, updates)

    def publish_heartbeat(self, worker_id: str, name: str = "AgentX Worker") -> Dict[str, Any]:
        return self.memory.publish_heartbeat(worker_id, name=name)

    def list_workers(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return self.memory.list_workers(status=status, limit=limit)

    def create_mission(self, goal: str) -> Dict[str, Any]:
        return self.memory.create_mission(goal)

    def update_mission(self, mission_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        return self.memory.update_mission(mission_id, updates)

    def list_missions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.memory.list_missions(status=status)

    def query_territory(self, query_vector: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        return self.memory.query_territory(query_vector, limit=limit)

    def add_runtime_event(self, event: Dict[str, Any]) -> str:
        return self.memory.add_runtime_event(event)

    def create_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self.memory.create_task(data)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.memory.get_task(task_id)

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        return self.memory.update_task(task_id, updates)

    def list_tasks(
        self,
        status: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.memory.list_tasks(status=status, statuses=statuses, limit=limit)
