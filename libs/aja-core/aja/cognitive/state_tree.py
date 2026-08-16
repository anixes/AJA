"""
=============================================================================
AJA Cognitive Architecture: System-2 State Tree & Checkpoint Management
=============================================================================
Maintains the hierarchical execution tree for Test-Time Compute (TTC) rollouts:
- Immutable state nodes with checkpoint snapshots
- Rollback diff tracking (file edits, variable changes)
- Tree navigation for dynamic backtracking upon failure
=============================================================================
"""

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StateNode:
    node_id: str
    parent_id: Optional[str]
    step_number: int
    action_type: str  # "reflex", "codeact_python", "codeact_shell", "tool_call", "checkpoint"
    action_payload: str
    observation: str = ""
    status: str = "pending"  # "pending", "success", "failed", "rolled_back"
    risk_score: float = 0.0  # 0.0 (safe) to 1.0 (critical)
    success_probability: float = 1.0
    checkpoint_state: Dict[str, Any] = field(default_factory=dict)
    children: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def record_observation(self, observation: str, success: bool = True) -> None:
        self.observation = observation
        self.status = "success" if success else "failed"


class StateTree:
    """
    Hierarchical execution tree supporting branching, state snapshots, and rollback rewinds.
    """

    def __init__(self, root_goal: str):
        self.tree_id = f"tree-{uuid.uuid4().hex[:8]}"
        self.root_goal = root_goal
        self.nodes: Dict[str, StateNode] = {}
        
        # Initialize root node
        root_node = StateNode(
            node_id="root",
            parent_id=None,
            step_number=0,
            action_type="root",
            action_payload=root_goal,
            status="success",
        )
        self.nodes["root"] = root_node
        self.active_node_id: str = "root"

    def create_child_node(
        self,
        parent_id: str,
        action_type: str,
        action_payload: str,
        risk_score: float = 0.0,
        checkpoint_state: Optional[Dict[str, Any]] = None,
    ) -> StateNode:
        """Appends a new child node to a parent in the state tree."""
        parent = self.nodes.get(parent_id)
        if not parent:
            raise ValueError(f"Parent node {parent_id} does not exist in tree.")

        node_id = f"node-{len(self.nodes)}-{uuid.uuid4().hex[:6]}"
        child = StateNode(
            node_id=node_id,
            parent_id=parent_id,
            step_number=parent.step_number + 1,
            action_type=action_type,
            action_payload=action_payload,
            risk_score=risk_score,
            checkpoint_state=checkpoint_state or {},
        )
        self.nodes[node_id] = child
        parent.children.append(node_id)
        self.active_node_id = node_id
        return child

    def record_observation(self, node_id: str, observation: str, success: bool) -> None:
        """Updates a node with execution observation and success/failure status."""
        node = self.nodes.get(node_id)
        if not node:
            return
        node.observation = observation
        node.status = "success" if success else "failed"

    def backtrack_to_parent(self, node_id: str) -> Optional[StateNode]:
        """
        Marks current node as rolled_back and reverts active pointer to the parent checkpoint.
        """
        node = self.nodes.get(node_id)
        if not node or not node.parent_id:
            return None

        node.status = "rolled_back"
        parent = self.nodes.get(node.parent_id)
        if parent:
            self.active_node_id = parent.node_id
        return parent

    def get_execution_path(self, target_node_id: Optional[str] = None) -> List[StateNode]:
        """Traces the active path from root to the specified or active node."""
        curr_id = target_node_id or self.active_node_id
        path: List[StateNode] = []
        while curr_id and curr_id in self.nodes:
            node = self.nodes[curr_id]
            path.append(node)
            curr_id = node.parent_id
        path.reverse()
        return path
