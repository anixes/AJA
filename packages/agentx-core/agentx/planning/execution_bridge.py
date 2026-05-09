"""
agentx/planning/execution_bridge.py
=====================================
Phase 11 - Execution Bridge.

Translates a PlanNode into an agentx `cmd_run` call without modifying the
existing engine or evaluator logic.  The bridge is a thin adapter:

  PlanNode  -  objective string + context dict  -  cmd_run()  -  update node status

Architecture notes
------------------
* We import `cmd_run` from `agentx` (the existing public entry-point).
* Context from completed predecessor nodes is injected via the `context`
  key in the input payload so the engine can see upstream outputs.
* TAO (Thought-Action-Observation) trace entries are emitted using the
  existing `log_event` persistence hook.
* On success the node is marked COMPLETED; on exception it is marked FAILED
  with the error stored in `node.error` for the replanner.

No code in `agentx/decision/` or `agentx/persistence/` is modified.
"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agentx.planning.models import PlanGraph, PlanNode


# ---------------------------------------------------------------------------
# Lazy imports - avoid circular dependency at module load time
# ---------------------------------------------------------------------------

def _cmd_run(objective: str, context: Optional[Dict[str, Any]] = None):
    """Thin wrapper that calls the existing agentx.cmd_run entry-point."""
    from agentx import cmd_run  # type: ignore[import]
    # cmd_run signature: cmd_run(objective, background=False, task=None)
    # We pass context inside the objective payload for now; a future refactor
    # can add a dedicated context parameter.
    task_payload = {"objective": objective}
    if context:
        task_payload["planning_context"] = context
    cmd_run(objective=objective, background=False, task=task_payload)


def _log_event(event: str, payload: Dict[str, Any]):
    try:
        from agentx.persistence.tracker import log_event  # type: ignore[import]
        log_event(event, payload)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ExecutionBridge
# ---------------------------------------------------------------------------

class ExecutionBridge:
    """
    Executes a single PlanNode through the existing AgentX engine.

    Parameters
    ----------
    graph : PlanGraph
        Used to resolve predecessor outputs for context injection.
    """

    def __init__(self, graph: PlanGraph, task_id: Optional[int] = None):
        self.graph = graph
        self.task_id = task_id
        self.system_state: Dict[str, Any] = {}
        self._state_lock = threading.Lock()
        
        from agentx.planning.execution_log import ExecutionLog
        self.log = ExecutionLog()

    # -- transactional execution --------------------------------------------

    def checkpoint_state(self, node_id: str) -> None:
        """Takes a deep copy snapshot of the current state before node execution."""
        with self._state_lock:
            self.log.checkpoint(node_id, self.system_state)

    def rollback_to(self, node_id: str) -> bool:
        """Restores the system state from the checkpoint taken before node_id."""
        with self._state_lock:
            if node_id in self.log.checkpoints:
                self.system_state = self.log.rollback(node_id)
                print(f"[ExecutionBridge] Rolled back state to before node '{node_id}'")
                return True
            print(f"[ExecutionBridge] Failed to rollback. No checkpoint found for '{node_id}'")
            return False

    # -- context assembly ---------------------------------------------------

    def _build_context(self, node: PlanNode) -> Dict[str, Any]:
        """
        Collect outputs from all completed predecessor nodes that this
        node explicitly lists in its `inputs` field.
        """
        ctx: Dict[str, Any] = {}
        for input_id in node.inputs:
            predecessor = self.graph.node_by_id(input_id)
            if predecessor and predecessor.status == "COMPLETED" and predecessor.result is not None:
                ctx[input_id] = predecessor.result
        return ctx

    # -- TAO trace helpers --------------------------------------------------

    def _emit_thought(self, node: PlanNode, context: Dict[str, Any]):
        _log_event("PLAN_NODE_THOUGHT", {
            "task_id": self.task_id,
            "node_id": node.id,
            "task": node.task[:120],
            "strategy": node.strategy,
            "context_keys": list(context.keys()),
            "uncertainty": node.uncertainty,
        })

    def _emit_action(self, node: PlanNode):
        _log_event("PLAN_NODE_ACTION", {
            "task_id": self.task_id,
            "node_id": node.id,
            "task": node.task[:120],
            "attempt": node.attempt,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

    def _emit_observation(self, node: PlanNode, success: bool):
        _log_event("PLAN_NODE_OBSERVATION", {
            "task_id": self.task_id,
            "node_id": node.id,
            "success": success,
            "status": node.status,
            "error": node.error or None,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })

    def _emit_debug_trace(
        self,
        node: PlanNode,
        preconditions_check: Dict[str, Any],
        effects_applied: Dict[str, Any],
        state_after: Dict[str, Any],
        status: str
    ):
        import os
        payload = {
            "task_id": self.task_id,
            "node_id": node.id,
            "preconditions_check": preconditions_check,
            "effects_applied": effects_applied,
            "state_after": dict(state_after),
            "status": status,
        }
        _log_event("PLAN_NODE_STATE_TRACE", payload)
        
        if os.environ.get("AGENTX_DEBUG_TRACE", "0") == "1":
            print(f"[DebugTrace] Node '{node.id}' - Status: {status}")
            print(f"             Preconditions: {preconditions_check}")
            print(f"             Effects applied: {effects_applied}")
            print(f"             State after: {state_after}")

    # -- execution ----------------------------------------------------------

    def run_node(self, node: PlanNode) -> bool:
        """
        Execute *node* through cmd_run.

        Returns True on success, False on failure.
        Mutates node.status, node.result, node.error, node.attempt in-place.
        """
        if node.is_compound:
            print(f"[ExecutionBridge] [FAIL] Node '{node.id}' FAILED: {node.error}")
            return False

        # -- Precondition Check --
        preconditions_check = {}
        precondition_failed = False
        
        with self._state_lock:
            for key, expected_value in node.preconditions.items():
                actual_value = self.system_state.get(key)
                match = (key in self.system_state and actual_value == expected_value)
                preconditions_check[key] = {
                    "expected": expected_value,
                    "actual": actual_value,
                    "match": match
                }
                if not match:
                    precondition_failed = True

        if precondition_failed:
            node.status = "FAILED"
            node.error = f"Precondition failed. Check: {preconditions_check}"
            print(f"[ExecutionBridge] [FAIL] Node '{node.id}' FAILED: {node.error}")
            self._emit_debug_trace(node, preconditions_check, {}, self.system_state, "FAILED")
            return False

        node.attempt += 1
        node.status = "RUNNING"

        context = self._build_context(node)

        self._emit_thought(node, context)
        self._emit_action(node)

        from agentx.runtime.event_bus import bus, EVENTS
        bus.publish(EVENTS["NODE_STARTED"], node)

        try:
            # Capability Execution instead of raw cmd_run
            from agentx.capabilities.registry import registry
            try:
                cap = registry.get(getattr(node, 'tool', 'agent.coder'))
            except KeyError:
                # Fallback to a default agent capability if not explicitly registered,
                # or fail the node if strict capabilities are enforced.
                # For compatibility with legacy nodes, we assume 'agent.coder'
                cap = registry.get('agent.coder')

            # Merge node inputs with built context
            cap_inputs = getattr(node, 'inputs', {}).copy()
            cap_inputs.update(context)
            cap_inputs['task'] = node.task

            result = cap.execute(cap_inputs)

            if not result.success:
                raise Exception(result.error or "Capability execution failed")

            # Apply capability state delta and node effects
            effects_applied = {}
            with self._state_lock:
                for k, v in result.state_delta.items():
                    self.system_state[k] = v
                    effects_applied[k] = v
                if node.effects:
                    for k, v in node.effects.items():
                        self.system_state[k] = v
                        effects_applied[k] = v

            node.status = "COMPLETED"
            node.result = result.output
            node.result["completed"] = True
            node.error = ""
            self._emit_observation(node, success=True)
            self._emit_debug_trace(node, preconditions_check, effects_applied, self.system_state, "SUCCESS")
            print(f"[ExecutionBridge] [OK] Node '{node.id}' COMPLETED (attempt {node.attempt})")
            
            with self._state_lock:
                self.log.record(node.id, self.system_state)
                
            bus.publish(EVENTS["NODE_SUCCESS"], node)
            return True

        except Exception as exc:
            node.status = "FAILED"
            node.error = f"{type(exc).__name__}: {exc}"
            self._emit_observation(node, success=False)
            self._emit_debug_trace(node, preconditions_check, {}, self.system_state, "FAILED")
            print(f"[ExecutionBridge] [FAIL] Node '{node.id}' FAILED: {node.error}")
            
            with self._state_lock:
                self.log.record(node.id, self.system_state)
                
            bus.publish(EVENTS["NODE_FAILED"], node)
            return False

    def run_wave(self, nodes: list) -> Dict[str, bool]:
        """
        Execute a list of nodes serially.  Returns a dict of {node_id: success}.
        A future version can parallelize this with ThreadPoolExecutor.
        """
        results: Dict[str, bool] = {}
        for node in nodes:
            results[node.id] = self.run_node(node)
        return results
