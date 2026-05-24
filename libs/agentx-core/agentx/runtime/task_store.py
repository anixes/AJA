from typing import Any, Dict, List, Optional, Protocol


class RuntimeTaskStore(Protocol):
    """Minimal task persistence contract needed by runtime schedulers."""

    def create_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        ...

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def list_tasks(
        self,
        status: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        ...


class LanceRuntimeTaskStore:
    """
    LanceDB-backed task store adapter.

    Keeps the scheduler dependency pointed at a runtime contract instead of the
    concrete client memory implementation.
    """

    def __init__(self, memory: Any = None):
        if memory is None:
            from agentx.runtime.lance_stores import LanceRuntimeStore

            memory = LanceRuntimeStore()
        self.memory = memory

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
