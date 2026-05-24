"""
aja/planning/models.py
==========================
Phase 11 - PlanNode and PlanGraph data model.

Strict schema - all fields required. Serialises to / from plain dicts
so the planner (LLM output), scheduler, and DB layer all share one type.

PlanNode fields
---------------
id              : unique snake_case identifier within the graph
task            : executable instruction passed verbatim to the engine
dependencies    : list of node IDs that must complete before this node runs
strategy        : execution mode  ["direct" | "skill" | "compose" | "swarm"]
inputs          : list of node IDs whose *outputs* are needed as context
outputs         : dict of key - description of data this node produces
dod             : Definition-of-Done contract used by the evaluator
uncertainty     : float 0.0-1.0 - controls retry / routing behaviour
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict


# ---------------------------------------------------------------------------
# DoD (Definition of Done) sub-structure
# ---------------------------------------------------------------------------

class DoD(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    success_criteria: str
    validation_type: str = "hybrid"  # "deterministic" | "semantic" | "hybrid"

    def __init__(self, *args, **kwargs):
        if args:
            # Support legacy positional arguments
            arg_names = ["success_criteria", "validation_type"]
            for i, arg in enumerate(args):
                if i < len(arg_names):
                    kwargs[arg_names[i]] = arg
        super().__init__(**kwargs)

    @field_validator("validation_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        valid = {"deterministic", "semantic", "hybrid"}
        if v not in valid:
            raise ValueError(
                f"DoD.validation_type must be one of {valid}, got '{v}'"
            )
        return v

    def to_dict(self) -> Dict[str, str]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: Dict[str, str]) -> "DoD":
        return cls.model_validate(d)


# ---------------------------------------------------------------------------
# PlanNode
# ---------------------------------------------------------------------------

VALID_STRATEGIES = {"direct", "skill", "compose", "swarm"}
VALID_NODE_TYPES = {"compound", "primitive"}


class PlanNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    task: str
    dependencies: List[str] = Field(default_factory=list)
    strategy: str = "direct"
    inputs: List[str] = Field(default_factory=list)
    outputs: Dict[str, str] = Field(default_factory=dict)
    preconditions: Dict[str, Any] = Field(default_factory=dict)
    effects: Dict[str, Any] = Field(default_factory=dict)
    
    dod: DoD = Field(default_factory=lambda: DoD(success_criteria="Task completes without error.", validation_type="deterministic"))
    uncertainty: float = 0.3
    risk: float = 0.0

    # HTN fields ----------------------------------------------------------
    node_type: str = Field(default="primitive", alias="type")
    children: List[str] = Field(default_factory=list)

    # Runtime-only fields
    status: str = "PENDING"   # PENDING | RUNNING | COMPLETED | FAILED
    result: Any = None
    error: str = ""
    attempt: int = 0

    def __init__(self, *args, **kwargs):
        if args:
            # Support legacy positional arguments from dataclass days
            arg_names = [
                "id", "task", "dependencies", "strategy", "inputs", "outputs",
                "preconditions", "effects", "dod", "uncertainty", "risk",
                "node_type", "children"
            ]
            for i, arg in enumerate(args):
                if i < len(arg_names):
                    kwargs[arg_names[i]] = arg
        super().__init__(**kwargs)

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        if v not in VALID_STRATEGIES:
            raise ValueError(
                f"PlanNode.strategy must be one of {VALID_STRATEGIES}, got '{v}'"
            )
        return v

    @field_validator("node_type")
    @classmethod
    def validate_node_type(cls, v: str) -> str:
        if v not in VALID_NODE_TYPES:
            raise ValueError(
                f"PlanNode.node_type must be one of {VALID_NODE_TYPES}, got '{v}'"
            )
        return v

    @field_validator("uncertainty")
    @classmethod
    def validate_uncertainty(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"PlanNode.uncertainty must be in [0, 1], got {v}")
        return v

    @model_validator(mode="after")
    def validate_required_fields(self) -> "PlanNode":
        if not self.id:
            raise ValueError("PlanNode.id must be a non-empty string")
        if not self.task:
            raise ValueError("PlanNode.task must be a non-empty string")
        return self

    # -- HTN helpers --------------------------------------------------------

    @property
    def is_primitive(self) -> bool:
        return self.node_type == "primitive"

    @property
    def is_compound(self) -> bool:
        return self.node_type == "compound"

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        data = self.model_dump(by_alias=True)
        # Exclude runtime fields for legacy compatibility
        for field_name in ["status", "result", "error", "attempt"]:
            if field_name in data:
                del data[field_name]
        return data

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanNode":
        # Handle the legacy 'type' -> 'node_type' mapping
        if "type" in d and "node_type" not in d:
            d["node_type"] = d.pop("type")
        
        # Effect Healing: LLMs often put logic in 'outputs' instead of 'effects'
        if not d.get("effects") and d.get("outputs"):
            d["effects"] = {k: True for k in d["outputs"].keys()}
            
        return cls.model_validate(d)


# ---------------------------------------------------------------------------
# PlanGraph
# ---------------------------------------------------------------------------

class PlanGraph(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    goal: str
    nodes: List[PlanNode] = Field(default_factory=list)

    def __init__(self, *args, **kwargs):
        if args:
            arg_names = ["goal", "nodes"]
            for i, arg in enumerate(args):
                if i < len(arg_names):
                    kwargs[arg_names[i]] = arg
        super().__init__(**kwargs)

    def node_by_id(self, node_id: str) -> PlanNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def root_nodes(self) -> List[PlanNode]:
        return [n for n in self.nodes if not n.dependencies]

    def primitive_nodes(self) -> List[PlanNode]:
        return [n for n in self.nodes if n.is_primitive]

    def compound_nodes(self) -> List[PlanNode]:
        return [n for n in self.nodes if n.is_compound]

    def leaf_primitives(self) -> List[PlanNode]:
        child_ids = {cid for n in self.nodes for cid in n.children}
        return [n for n in self.nodes if n.is_primitive and n.id not in child_ids]

    @property
    def edges(self) -> List[tuple[str, str]]:
        """Return all edges in the graph as (source, target) tuples."""
        edges = []
        for n in self.nodes:
            for dep in n.dependencies:
                edges.append((dep, n.id))
            for child in n.children:
                edges.append((n.id, child))
        return edges

    def children_of(self, node_id: str) -> List[PlanNode]:
        parent = self.node_by_id(node_id)
        if parent is None:
            return []
        return [n for cid in parent.children for n in [self.node_by_id(cid)] if n is not None]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "nodes": [n.to_dict() for n in self.nodes],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanGraph":
        """
        Instantiate PlanGraph from dict, with semantic-to-ID mapping for inputs.
        """
        # 1. First pass: Basic validation
        graph = cls.model_validate(d)
        
        # 2. Semantic Healing pass
        # If any node has 'inputs' that aren't node IDs, check if they are output keys
        # of previous nodes and map them to the ID of that node.
        node_ids = {n.id for n in graph.nodes}
        output_key_map = {}
        for n in graph.nodes:
            for k in n.outputs.keys():
                output_key_map[k] = n.id
                
        for n in graph.nodes:
            new_inputs = []
            for inp in n.inputs:
                if inp in node_ids:
                    new_inputs.append(inp)
                elif inp in output_key_map:
                    # Map semantic name to the producing node ID
                    producer_id = output_key_map[inp]
                    if producer_id != n.id: # Avoid self-ref
                        new_inputs.append(producer_id)
                # Hallucinated names are dropped to avoid validation errors
            n.inputs = list(set(new_inputs))
            
        # 3. HTN Structural Healing
        # - Remove child -> parent dependencies
        # - Remove compound dependencies (target their leaf primitives instead)
        # - Ensure primitive nodes have no children
        
        # Build child-to-parent map
        parent_map = {}
        for n in graph.nodes:
            for cid in n.children:
                parent_map[cid] = n.id
                
        compound_ids = {n.id for n in graph.nodes if n.is_compound}
        
        for n in graph.nodes:
            # 3a. Remove parent from dependencies
            if n.id in parent_map:
                p_id = parent_map[n.id]
                if p_id in n.dependencies:
                    n.dependencies = [d for d in n.dependencies if d != p_id]
            
            # 3b. Remove children if primitive
            if n.is_primitive and n.children:
                n.children = []
                
            # 3c. Redirect compound dependencies to their first leaf child (simplification)
            new_deps = []
            for dep_id in n.dependencies:
                dep_node = graph.node_by_id(dep_id)
                if dep_node and dep_node.is_compound:
                    # Find leaf primitives of this compound
                    leaves = []
                    to_check = list(dep_node.children)
                    visited = set()
                    while to_check:
                        curr_id = to_check.pop(0)
                        if curr_id in visited: continue
                        visited.add(curr_id)
                        curr_node = graph.node_by_id(curr_id)
                        if not curr_node: continue
                        if curr_node.is_primitive:
                            leaves.append(curr_id)
                        else:
                            to_check.extend(curr_node.children)
                    if leaves:
                        new_deps.extend(leaves)
                else:
                    new_deps.append(dep_id)
            n.dependencies = list(set(new_deps))

        return graph

    @classmethod
    def from_json(cls, raw: str) -> "PlanGraph":
        return cls.from_dict(json.loads(raw))

    def __repr__(self) -> str:
        return f"PlanGraph(goal={self.goal!r}, nodes={len(self.nodes)})"


# ---------------------------------------------------------------------------
# PlanVersion
# ---------------------------------------------------------------------------

class PlanVersion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent: Optional[str] = None
    plan: PlanGraph = Field(default_factory=lambda: PlanGraph(goal=""))
    timestamp: float = Field(default_factory=time.time)
    label: str = "initial"

    def __init__(self, *args, **kwargs):
        if args:
            arg_names = ["id", "parent", "plan", "timestamp", "label"]
            for i, arg in enumerate(args):
                if i < len(arg_names):
                    kwargs[arg_names[i]] = arg
        super().__init__(**kwargs)

    @property
    def iso_timestamp(self) -> str:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        data = self.model_dump()
        data["iso_timestamp"] = self.iso_timestamp
        return data

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanVersion":
        return cls.model_validate(d)

    def __repr__(self) -> str:
        pid = (self.parent[:8] + '...') if self.parent else 'None'
        return (
            f"PlanVersion(id={self.id[:8]}..., parent={pid}, "
            f"label={self.label!r}, t={self.iso_timestamp})"
        )
