import copy
from typing import Dict, Any, List

class ExecutionLog:
    def __init__(self):
        self.entries: List[Dict[str, Any]] = []   # ordered log
        self.checkpoints: Dict[str, Dict[str, Any]] = {}

    def record(self, node_id: str, state_snapshot: Dict[str, Any]):
        self.entries.append({
            "node": node_id,
            "state": copy.deepcopy(state_snapshot)
        })

    def checkpoint(self, node_id: str, state: Dict[str, Any]):
        self.checkpoints[node_id] = copy.deepcopy(state)

    def rollback(self, node_id: str) -> Dict[str, Any]:
        return copy.deepcopy(self.checkpoints[node_id])
