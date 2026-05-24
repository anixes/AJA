"""Runtime persistence protocols.

These contracts describe runtime ownership without binding runtime code to the
current AJA-named LanceDB physical tables.
"""

from typing import Any, Dict, List, Optional, Protocol


class RuntimeApprovalStore(Protocol):
    def get_active_approval(self) -> Optional[Dict[str, Any]]:
        ...

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        ...

    def create_approval(self, data: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def update_approval(self, approval_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        ...


class RuntimeWorkerStore(Protocol):
    def publish_heartbeat(self, worker_id: str, name: str = "AgentX Worker") -> Dict[str, Any]:
        ...

    def list_workers(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        ...


class RuntimeMissionStore(Protocol):
    def create_mission(self, goal: str) -> Dict[str, Any]:
        ...

    def update_mission(self, mission_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def list_missions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        ...


class RuntimeKnowledgeStore(Protocol):
    def query_territory(self, query_vector: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        ...
