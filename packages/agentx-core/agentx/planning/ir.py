from typing import Dict, List, Any, Optional

class NodePolicy:
    def __init__(self, retry: int = 0, timeout: int = 30, idempotent: bool = False, compensation: Optional[str] = None):
        self.retry = retry
        self.timeout = timeout
        self.idempotent = idempotent
        self.compensation = compensation

class PlanNode:
    def __init__(self, id: str, tool: str, inputs: Dict[str, Any], policy: Optional[Dict[str, Any]] = None):
        self.id = id
        self.tool = tool
        self.inputs = inputs
        # Apply default policy if none provided
        p = policy or {}
        self.policy = NodePolicy(
            retry=p.get("retry", 0),
            timeout=p.get("timeout", 30),
            idempotent=p.get("idempotent", False),
            compensation=p.get("compensation", None)
        )

class PlanIR:
    """Canonical Execution Intermediate Representation for AgentX."""
    def __init__(self, nodes: Dict[str, PlanNode], edges: Dict[str, List[str]], metadata: Optional[Dict[str, Any]] = None):
        self.nodes = nodes
        self.edges = edges
        self.metadata = metadata or {}
        
    @classmethod
    def from_dag(cls, dag_dict: dict) -> 'PlanIR':
        nodes = {}
        for node_id, node_data in dag_dict.get("nodes", {}).items():
            nodes[node_id] = PlanNode(
                id=node_id, 
                tool=node_data.get("tool", "agent.coder"), 
                inputs=node_data.get("inputs", {}),
                policy=node_data.get("policy", {})
            )
        edges = dag_dict.get("edges", {})
        return cls(nodes=nodes, edges=edges)

    def to_dict(self) -> dict:
        """Utility for compatibility with older dict-based components."""
        return {
            "nodes": {
                node_id: {
                    "tool": node.tool,
                    "inputs": node.inputs,
                    "policy": {
                        "retry": node.policy.retry,
                        "timeout": node.policy.timeout,
                        "idempotent": node.policy.idempotent,
                        "compensation": node.policy.compensation
                    }
                }
                for node_id, node in self.nodes.items()
            },
            "edges": self.edges,
            "metadata": self.metadata
        }
