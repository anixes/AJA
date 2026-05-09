"""
agentx/planning/replanner.py
=============================
Phase 11 - Dynamic Replanner.

Classifies a failed PlanNode's error and decides the best recovery
strategy.  Keeps a repair history so the same node isn't retried
indefinitely.

Failure taxonomy
----------------
AUTH_ERROR      - Permanent; skip node, escalate
RATE_LIMIT      - Retry with exponential backoff
TOOL_NOT_FOUND  - Try alternative strategy (skill - direct)
CONTEXT_MISSING - Inject missing dependency, restart node
TIMEOUT         - Retry with simpler task decomposition
UNKNOWN         - Retry up to MAX_RETRIES, then mark permanent

Recovery actions
----------------
RETRY           - Requeue node with incremented attempt counter
SKIP            - Mark node SKIPPED_PERMANENT; unblock successors
DECOMPOSE       - Break node into sub-nodes (basic heuristic split)
ESCALATE        - Surface to human / circuit-breaker

The Replanner does NOT modify the DAGValidator or engine - it only
mutates node-level fields (status, task, error) and optionally injects
new PlanNode objects into the graph.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

from agentx.planning.models import PlanGraph, PlanNode, DoD


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES: int = 3
BASE_BACKOFF_SECONDS: float = 2.0   # multiplied by 2^attempt
UNCERTAINTY_HARD_LIMIT: float = 0.8


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

class FailureKind(Enum):
    AUTH_ERROR = auto()
    RATE_LIMIT = auto()
    TOOL_NOT_FOUND = auto()
    CONTEXT_MISSING = auto()
    TIMEOUT = auto()
    UNKNOWN = auto()


_PATTERNS: List[Tuple[re.Pattern, FailureKind]] = [
    (re.compile(r"auth|unauthori[zs]ed|403|401", re.I), FailureKind.AUTH_ERROR),
    (re.compile(r"rate.-limit|429|too many request", re.I), FailureKind.RATE_LIMIT),
    (re.compile(r"tool.-not.-found|no such tool|unknown skill", re.I), FailureKind.TOOL_NOT_FOUND),
    (re.compile(r"context|missing.-input|key.-error", re.I), FailureKind.CONTEXT_MISSING),
    (re.compile(r"timeout|timed.-out|deadline", re.I), FailureKind.TIMEOUT),
]


def classify_error(error: str) -> FailureKind:
    for pattern, kind in _PATTERNS:
        if pattern.search(error):
            return kind
    return FailureKind.UNKNOWN


# ---------------------------------------------------------------------------
# Recovery action
# ---------------------------------------------------------------------------

class RecoveryAction(Enum):
    RETRY = "RETRY"
    SKIP = "SKIP"
    DECOMPOSE = "DECOMPOSE"
    ESCALATE = "ESCALATE"


# ---------------------------------------------------------------------------
# Repair record
# ---------------------------------------------------------------------------

@dataclass
class RepairRecord:
    node_id: str
    attempt: int
    failure_kind: str
    action_taken: str
    timestamp: str
    notes: str = ""


# ---------------------------------------------------------------------------
# Replanner
# ---------------------------------------------------------------------------

class Replanner:
    """
    Analyses a FAILED PlanNode and applies the appropriate recovery.

    Parameters
    ----------
    graph : PlanGraph
        The live plan graph (mutated in-place during repair).
    repair_history : list
        Running list of RepairRecord objects (injected for testability).
    """

    def __init__(self, graph: PlanGraph, repair_history: Optional[List[RepairRecord]] = None):
        self.graph = graph
        self.repair_history: List[RepairRecord] = repair_history if repair_history is not None else []

    # -- public API ---------------------------------------------------------

    def handle_failure(self, node: PlanNode) -> RecoveryAction:
        """
        Classify the failure and apply a recovery strategy.

        Returns the RecoveryAction that was applied so the caller
        (ReActExecutor) can decide what to do next.
        """
        kind = classify_error(node.error)
        action = self._decide_action(node, kind)
        self._apply(node, kind, action)

        record = RepairRecord(
            node_id=node.id,
            attempt=node.attempt,
            failure_kind=kind.name,
            action_taken=action.value,
            timestamp=_iso_now(),
            notes=node.error[:200],
        )
        self.repair_history.append(record)
        self._log(record)
        return action

    # -- decision logic -----------------------------------------------------

    def _decide_action(self, node: PlanNode, kind: FailureKind) -> RecoveryAction:
        if node.attempt >= MAX_RETRIES:
            return RecoveryAction.ESCALATE

        if kind == FailureKind.AUTH_ERROR:
            return RecoveryAction.ESCALATE          # can't retry auth failures

        if kind == FailureKind.RATE_LIMIT:
            return RecoveryAction.RETRY             # backoff + retry

        if kind == FailureKind.TOOL_NOT_FOUND:
            return RecoveryAction.RETRY             # strategy downgrade then retry

        if kind == FailureKind.CONTEXT_MISSING:
            return RecoveryAction.RETRY             # wait for predecessors

        if kind == FailureKind.TIMEOUT:
            # Tasks with multiple sentences (> 50 chars) qualify for decomposition
            return RecoveryAction.DECOMPOSE if len(node.task) > 50 else RecoveryAction.RETRY

        return RecoveryAction.RETRY                 # UNKNOWN - optimistic retry

    # -- recovery actions ---------------------------------------------------

    def _apply(self, node: PlanNode, kind: FailureKind, action: RecoveryAction) -> None:
        if action == RecoveryAction.RETRY:
            self._apply_retry(node, kind)
        elif action == RecoveryAction.SKIP:
            self._apply_skip(node)
        elif action == RecoveryAction.DECOMPOSE:
            self._apply_decompose(node)
        elif action == RecoveryAction.ESCALATE:
            self._apply_escalate(node)

    def _apply_retry(self, node: PlanNode, kind: FailureKind) -> None:
        backoff = BASE_BACKOFF_SECONDS * (2 ** (node.attempt - 1))

        if kind == FailureKind.TOOL_NOT_FOUND:
            # Downgrade strategy: skill - direct
            _DOWNGRADE = {"skill": "direct", "compose": "direct", "swarm": "direct"}
            node.strategy = _DOWNGRADE.get(node.strategy, "direct")
            print(f"[Replanner] Node '{node.id}' strategy downgraded to '{node.strategy}'")

        if kind == FailureKind.RATE_LIMIT:
            print(f"[Replanner] Rate-limit backoff {backoff:.1f}s for node '{node.id}'")
            time.sleep(backoff)

        node.status = "PENDING"
        node.error = ""

    def _apply_skip(self, node: PlanNode) -> None:
        node.status = "FAILED"
        node.error = f"[Replanner] Skipped after {node.attempt} attempts: {node.error}"
        print(f"[Replanner] [SKIP] Node '{node.id}' SKIPPED permanently")

    def _apply_decompose(self, node: PlanNode) -> None:
        """
        Heuristic decomposition: split a long task at sentence boundaries.
        Inserts two child nodes and marks the parent COMPLETED (delegated).
        """
        sentences = [s.strip() for s in node.task.split(".") if s.strip()]
        if len(sentences) < 2:
            # Can't decompose sensibly - fall back to retry
            node.status = "PENDING"
            node.error = ""
            return

        child_a_id = f"{node.id}_part_a"
        child_b_id = f"{node.id}_part_b"

        child_a = PlanNode(
            id=child_a_id,
            task=sentences[0] + ".",
            dependencies=node.dependencies,
            strategy=node.strategy,
            inputs=node.inputs,
            outputs={},
            dod=DoD(node.dod.success_criteria, node.dod.validation_type),
            uncertainty=node.uncertainty,
        )
        child_b = PlanNode(
            id=child_b_id,
            task=". ".join(sentences[1:]) + ".",
            dependencies=[child_a_id] + node.dependencies,
            strategy=node.strategy,
            inputs=[child_a_id] + node.inputs,
            outputs=node.outputs,
            dod=DoD(node.dod.success_criteria, node.dod.validation_type),
            uncertainty=node.uncertainty,
        )

        # Reroute successors to depend on child_b instead of original node
        for other in self.graph.nodes:
            if node.id in other.dependencies:
                other.dependencies = [
                    child_b_id if d == node.id else d for d in other.dependencies
                ]

        self.graph.nodes.extend([child_a, child_b])
        node.status = "COMPLETED"   # parent delegated
        print(
            f"[Replanner] [DECOMPOSE] Node '{node.id}' decomposed -> "
            f"'{child_a_id}' + '{child_b_id}'"
        )

    def _apply_escalate(self, node: PlanNode) -> None:
        node.status = "FAILED"
        node.error = (
            f"[Replanner] ESCALATED after {node.attempt} attempt(s). "
            f"Last error: {node.error}"
        )
        print(f"[Replanner] [ESCALATE] Node '{node.id}' ESCALATED - manual intervention required")
        try:
            from agentx.persistence.tracker import log_event
            log_event("PLAN_NODE_ESCALATED", {"node_id": node.id, "error": node.error})
        except Exception:
            pass

    # -- localized repair ---------------------------------------------------

    def find_failure_scope(self, failed_node: PlanNode) -> List[PlanNode]:
        """
        Finds the failed node and all downstream dependent nodes.
        Independent completed nodes are excluded.
        """
        scope_ids = {failed_node.id}
        changed = True
        
        while changed:
            changed = False
            for node in self.graph.nodes:
                if node.id not in scope_ids:
                    # If any dependency is in the scope, this node is in the scope
                    if any(dep in scope_ids for dep in node.dependencies):
                        scope_ids.add(node.id)
                        changed = True
                        
        return [n for n in self.graph.nodes if n.id in scope_ids]

    def extract_subtree(self, failed_node: PlanNode) -> PlanGraph:
        """
        Extracts a minimal repairable PlanGraph unit starting from the failed node.
        """
        scope_nodes = self.find_failure_scope(failed_node)
        import copy
        subtree = PlanGraph(
            goal=f"Recover from failure at '{failed_node.task}'",
            nodes=[copy.deepcopy(n) for n in scope_nodes]
        )
        # Update dependencies within the subtree
        scope_ids = {n.id for n in scope_nodes}
        for node in subtree.nodes:
            node.dependencies = [d for d in node.dependencies if d in scope_ids]
            node.inputs = [i for i in node.inputs if i in scope_ids]
            
        return subtree

    def repair_subtree(self, failed_node: PlanNode, state: Dict[str, Any]) -> bool:
        """
        Invokes the Planner to generate a repair for the failed subtree.
        If successful, swaps it in.
        Returns True if repaired, False otherwise.
        """
        print(f"[Replanner] Attempting localized repair for node '{failed_node.id}'...")
        
        # 1. Extract the broken piece
        scope_nodes = self.find_failure_scope(failed_node)
        
        # We define the repair goal as achieving the effects of the nodes in the scope
        # Usually, the simplest repair is re-planning the task of the failed node.
        repair_goal = f"Successfully complete: {failed_node.task}"
        
        from agentx.planning.planner import Planner
        temp_planner = Planner(use_method_routing=False) # Fallback directly to LLM for repair
        
        try:
            # We bypass method cache because we specifically want a generative repair
            new_subtree = temp_planner.decompose(repair_goal, state)
            
            if new_subtree and len(new_subtree.nodes) > 0 and new_subtree.nodes[0].id != "execute_goal":
                self.replace_subtree(scope_nodes, new_subtree, failed_node)
                print(f"[Replanner] Successfully applied localized repair for '{failed_node.id}'")
                return True
        except Exception as e:
            print(f"[Replanner] Localized repair generation failed: {e}")
            
        return False

    def replace_subtree(self, old_scope: List[PlanNode], new_subtree: PlanGraph, failed_node: PlanNode) -> None:
        """
        Swaps the old scope with the new subtree, rewiring dependencies.
        """
        old_ids = {n.id for n in old_scope}
        
        # Keep nodes that are not in the old scope
        preserved_nodes = [n for n in self.graph.nodes if n.id not in old_ids]
        
        # Find the exit points of the new subtree (nodes with no successors inside the subtree)
        new_ids = {n.id for n in new_subtree.nodes}
        new_exit_nodes = []
        for n in new_subtree.nodes:
            is_depended_on = False
            for other in new_subtree.nodes:
                if n.id in other.dependencies:
                    is_depended_on = True
                    break
            if not is_depended_on:
                new_exit_nodes.append(n.id)
                
        if not new_exit_nodes:
            new_exit_nodes = list(new_ids) # Fallback

        # Add the new nodes to the graph
        preserved_nodes.extend(new_subtree.nodes)
        
        # Wire the new subtree's roots to depend on the failed node's old dependencies
        for n in new_subtree.nodes:
            if not n.dependencies:
                n.dependencies.extend(failed_node.dependencies)
                n.inputs.extend(failed_node.inputs)

        # Wire any preserved node that depended on an old scope node to depend on the new exit nodes
        for n in preserved_nodes:
            if n.id not in new_ids:
                has_old_dep = any(dep in old_ids for dep in n.dependencies)
                if has_old_dep:
                    # Remove old deps
                    n.dependencies = [d for d in n.dependencies if d not in old_ids]
                    # Add new exit deps
                    n.dependencies.extend(new_exit_nodes)
                    
                has_old_input = any(i in old_ids for i in n.inputs)
                if has_old_input:
                    n.inputs = [i for i in n.inputs if i not in old_ids]
                    n.inputs.extend(new_exit_nodes)
                    
        # Update the graph nodes
        self.graph.nodes = preserved_nodes

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _log(record: RepairRecord) -> None:
        print(
            f"[Replanner] node={record.node_id} attempt={record.attempt} "
            f"kind={record.failure_kind} action={record.action_taken}"
        )


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
