"""
agentx/planning/dag_validator.py
=================================
Phase 11 - DAG Validator.

Two checks are enforced before any execution:

1. **Dependency integrity** - every id listed in `node.dependencies` must
   exist in the graph's node set.

2. **Cycle detection** - uses iterative DFS (Kahn's algorithm variant with
   in-degree tracking) to detect cycles without recursion overflow risk.

3. **Uncertainty gate** - nodes with uncertainty > UNCERTAINTY_HARD_LIMIT
   are flagged as violations (the replanner handles them, not the validator).

Usage
-----
    result = DAGValidator.validate(graph)
    if not result.ok:
        print(result.errors)
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import List, Set

from agentx.planning.models import PlanGraph


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNCERTAINTY_HARD_LIMIT: float = 0.8


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def __str__(self) -> str:
        if self.ok:
            return "ValidationResult: OK"
        return "ValidationResult: FAILED\n  " + "\n  ".join(self.errors)


# ---------------------------------------------------------------------------
# DAGValidator
# ---------------------------------------------------------------------------

class DAGValidator:
    """Static validator - all methods are class-level, no state."""

    @classmethod
    def validate(cls, graph: PlanGraph) -> ValidationResult:
        """
        Run all checks and return a consolidated ValidationResult.
        """
        result = ValidationResult(ok=True)
        node_ids: Set[str] = {n.id for n in graph.nodes}

        cls._check_empty(graph, result)
        cls._check_unique_ids(graph, result)
        cls._check_dependencies(graph, node_ids, result)
        cls._check_self_loops(graph, result)
        cls._check_cycles(graph, node_ids, result)
        cls._check_uncertainty(graph, result)
        cls._check_htn_structure(graph, node_ids, result)   # HTN invariants

        if result.ok:
            state_result = cls.validate_state_flow(graph)
            if not state_result.ok:
                result.ok = False
                result.errors.extend(state_result.errors)

        return result

    # -- individual checks --------------------------------------------------

    @staticmethod
    def _check_empty(graph: PlanGraph, result: ValidationResult) -> None:
        if not graph.nodes:
            result.add_error("PlanGraph has no nodes - cannot execute an empty plan.")

    @staticmethod
    def _check_unique_ids(graph: PlanGraph, result: ValidationResult) -> None:
        seen: Set[str] = set()
        for node in graph.nodes:
            if node.id in seen:
                result.add_error(f"Duplicate node id: '{node.id}'")
            seen.add(node.id)

    @staticmethod
    def _check_dependencies(
        graph: PlanGraph, node_ids: Set[str], result: ValidationResult
    ) -> None:
        for node in graph.nodes:
            for dep in node.dependencies:
                if dep not in node_ids:
                    result.add_error(
                        f"Node '{node.id}' depends on unknown id '{dep}'"
                    )
            for inp in node.inputs:
                if inp not in node_ids:
                    result.add_error(
                        f"Node '{node.id}' lists unknown input id '{inp}'"
                    )

    @staticmethod
    def _check_self_loops(graph: PlanGraph, result: ValidationResult) -> None:
        for node in graph.nodes:
            if node.id in node.dependencies:
                result.add_error(f"Node '{node.id}' depends on itself (self-loop)")

    @staticmethod
    def _check_cycles(
        graph: PlanGraph, node_ids: Set[str], result: ValidationResult
    ) -> None:
        """Kahn's algorithm: if topological sort consumes all nodes there's no cycle."""
        in_degree: dict = defaultdict(int)
        adj: dict = defaultdict(list)

        for node in graph.nodes:
            in_degree.setdefault(node.id, 0)
            for dep in node.dependencies:
                if dep in node_ids:          # only track valid deps
                    adj[dep].append(node.id)
                    in_degree[node.id] += 1

        queue = deque(nid for nid in node_ids if in_degree[nid] == 0)
        visited = 0

        while queue:
            nid = queue.popleft()
            visited += 1
            for successor in adj[nid]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        if visited < len(node_ids):
            result.add_error(
                "Cycle detected in PlanGraph - the graph is NOT a DAG. "
                f"({len(node_ids) - visited} node(s) unreachable via topological sort)"
            )

    @staticmethod
    def _check_uncertainty(graph: PlanGraph, result: ValidationResult) -> None:
        for node in graph.nodes:
            if node.uncertainty > UNCERTAINTY_HARD_LIMIT:
                result.add_error(
                    f"Node '{node.id}' has uncertainty {node.uncertainty:.2f} "
                    f"> hard limit {UNCERTAINTY_HARD_LIMIT} - must be replanned before execution"
                )

    # -- HTN structure check ------------------------------------------------

    @staticmethod
    def _check_htn_structure(
        graph: PlanGraph, node_ids: Set[str], result: ValidationResult
    ) -> None:
        """
        Enforce Hierarchical Task Network invariants:

        1. A ``compound`` node MUST declare at least one child - a compound
           node with no children is an empty organiser with no execution path.

        2. A ``primitive`` node MUST NOT declare children - primitive nodes
           are leaf tasks executed directly by the engine; they cannot have
           sub-structure.

        3. Every child ID listed in a compound node MUST reference a node
           that exists in the graph (referential integrity for the HTN tree).

        4. A compound node MUST NOT appear in a ``dependencies`` list unless
           it can be resolved to a set of primitive descendants (structural
           nodes are not executable, so depending on one directly is an
           error - depend on its leaf primitives instead).
        """
        for node in graph.nodes:
            if node.is_compound:
                # Rule 1 - compound must have children
                if not node.children:
                    result.add_error(
                        f"Compound node '{node.id}' has no children - "
                        "every compound node must decompose into at least one child."
                    )
                # Rule 3 - child IDs must exist
                for cid in node.children:
                    if cid not in node_ids:
                        result.add_error(
                            f"Compound node '{node.id}' references unknown child '{cid}'"
                        )
            elif node.is_primitive:
                # Rule 2 - primitive must not have children
                if node.children:
                    result.add_error(
                        f"Primitive node '{node.id}' must not declare children "
                        f"(found: {node.children}). Split it into a compound node instead."
                    )

        # Rule 4 - no dependency on a compound node
        compound_ids = {n.id for n in graph.nodes if n.is_compound}
        for node in graph.nodes:
            for dep in node.dependencies:
                if dep in compound_ids:
                    result.add_error(
                        f"Node '{node.id}' depends on compound node '{dep}'. "
                        "Dependencies must target primitive nodes only - "
                        "depend on the leaf primitives of the compound subtree instead."
                    )

    # -- state flow verification ---------------------------------------------

    @classmethod
    def validate_state_flow(cls, graph: PlanGraph) -> ValidationResult:
        """
        Validates the state flow of the HTN plan across all dependency chains.
        - ensures every dependency chain satisfies: effects(parent) -> preconditions(child)
        - detects contradictions in state assignments
        - detects unreachable nodes due to unmet state
        """
        result = ValidationResult(ok=True)
        primitives = {n.id: n for n in graph.nodes if n.is_primitive}

        # Build DAG for primitives to compute topological order and ancestors
        adj = defaultdict(list)
        in_degree = defaultdict(int)
        for nid in primitives:
            in_degree[nid] = 0

        for nid, node in primitives.items():
            for dep in node.dependencies:
                if dep in primitives:
                    adj[dep].append(nid)
                    in_degree[nid] += 1

        queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
        topo_order = []
        while queue:
            curr = queue.popleft()
            topo_order.append(curr)
            for succ in adj[curr]:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        if len(topo_order) < len(primitives):
            result.add_error("Cycle detected in primitive nodes; cannot validate state flow.")
            return result

        ancestors = defaultdict(set)
        for nid in topo_order:
            for succ in adj[nid]:
                ancestors[succ].add(nid)
                ancestors[succ].update(ancestors[nid])

        # 1. Detect contradictions in state assignments
        key_writers = defaultdict(list)
        for nid in topo_order:
            for k in primitives[nid].effects:
                key_writers[k].append(nid)

        for k, writers in key_writers.items():
            for i in range(len(writers)):
                for j in range(i + 1, len(writers)):
                    u, v = writers[i], writers[j]
                    if u not in ancestors[v] and v not in ancestors[u]:
                        result.add_error(
                            f"State flow contradiction: Parallel nodes '{u}' and '{v}' "
                            f"both assign effect key '{k}'."
                        )

        # 2. Check reachability / unmet preconditions
        for nid in topo_order:
            node = primitives[nid]
            node_ancestors = ancestors[nid]
            
            available_state = {}
            for a in topo_order:
                if a in node_ancestors:
                    available_state.update(primitives[a].effects)

            for pk, expected_val in node.preconditions.items():
                if pk not in available_state:
                    result.add_error(
                        f"Unreachable node '{nid}': Precondition '{pk}' is not met by any predecessor."
                    )
                elif available_state[pk] != expected_val:
                    result.add_error(
                        f"Unmet state for '{nid}': Precondition '{pk}' expected {expected_val}, "
                        f"but upstream provides {available_state[pk]}."
                    )

        return result

