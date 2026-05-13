"""
agentx/planning/react_executor.py
===================================
Phase 11 - ReAct-style Executor.

Implements the core "Reason - Act - Observe" loop over a PlanGraph:

  1. **Reason**  - Scheduler identifies the next wave of ready nodes.
  2. **Act**     - ExecutionBridge runs each node through cmd_run.
  3. **Observe** - Result is inspected; Replanner is invoked on failures.
  4. **Persist** - PlanStore snapshots the graph state after each wave.

Hard-stop conditions
--------------------
* All nodes COMPLETED            - success
* A node ESCALATED and no more ready nodes - partial-failure exit
* Max wave iterations exceeded   - safety cap

The executor honours the `PLANNING_ENABLED` feature flag so the existing
agent_loop can safely gate its use.

Usage
-----
    executor = ReActExecutor(plan_id="abc123", graph=plan_graph)
    success = executor.run()
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from agentx.planning.models import PlanGraph, PlanNode
from agentx.planning.scheduler import Scheduler
from agentx.planning.execution_bridge import ExecutionBridge
from agentx.planning.replanner import Replanner, RecoveryAction, RepairRecord
from agentx.planning.plan_store import PlanStore
from agentx.planning.version_store import VersionStore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_WAVES: int = 50     # safety cap - prevents infinite replanning loops


# ---------------------------------------------------------------------------
# ReActExecutor
# ---------------------------------------------------------------------------

class ReActExecutor:
    """
    Runs a PlanGraph to completion using a ReAct (Reason-Act-Observe) loop.

    Parameters
    ----------
    graph : PlanGraph
        Validated DAG ready for execution.
    plan_id : str, optional
        Stable identifier for PlanStore persistence.
        Auto-generated if not provided.
    """

    def __init__(self, graph: PlanGraph, plan_id: Optional[str] = None, task_id: Optional[int] = None, session=None):
        self.graph = graph
        self.plan_id = plan_id or str(uuid.uuid4())
        self.task_id = task_id
        self.session = session
        self.repair_history: List[RepairRecord] = []
        self._bridge = ExecutionBridge(self.graph, task_id=self.task_id)
        self._replanner = Replanner(self.graph, self.repair_history)
        # Cut initial version snapshot
        initial = VersionStore.cut(self.plan_id, self.graph, parent_id=None, label="initial")
        self.current_version_id: str = initial.id

    # -- public API ---------------------------------------------------------

    def _split_into_safe_batches(self, nodes: List[PlanNode]) -> List[List[PlanNode]]:
        """
        Split nodes into batches by building a conflict graph to ensure
        conflict-serializable execution that perfectly matches sequential execution.
        """
        from collections import defaultdict
        
        adj = defaultdict(list)
        in_degree = {n.id: 0 for n in nodes}
        node_map = {n.id: n for n in nodes}

        # Build conflict graph (DAG based on original deterministic order)
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                node_a = nodes[i]
                node_b = nodes[j]

                effects_a = set(node_a.effects.keys()) if node_a.effects else set()
                effects_b = set(node_b.effects.keys()) if node_b.effects else set()
                preconds_a = set(node_a.preconditions.keys()) if node_a.preconditions else set()
                preconds_b = set(node_b.preconditions.keys()) if node_b.preconditions else set()

                if (effects_a & effects_b) or (preconds_a & effects_b) or (preconds_b & effects_a):
                    # node_a MUST precede node_b to preserve sequential equivalence
                    adj[node_a.id].append(node_b.id)
                    in_degree[node_b.id] += 1

        # Topological sort into layers (batches)
        batches: List[List[PlanNode]] = []
        while True:
            # Find all nodes with no incoming conflict edges
            ready_ids = [nid for nid, deg in in_degree.items() if deg == 0]
            if not ready_ids:
                if in_degree:
                    # Cycle detected (should be impossible due to i < j construction)
                    raise RuntimeError("Cycle detected in conflict graph.")
                break

            # Sort to preserve deterministic sub-ordering
            ready_ids.sort()
            batch = [node_map[nid] for nid in ready_ids]
            batches.append(batch)

            # Remove these nodes and decrement successors
            for nid in ready_ids:
                del in_degree[nid]
                for successor_id in adj[nid]:
                    if successor_id in in_degree:
                        in_degree[successor_id] -= 1

        return batches

    def run(self) -> bool:
        """
        Execute the plan to completion.

        Returns
        -------
        bool - True if all nodes COMPLETED, False otherwise.
        """
        print(
            f"\n[ReActExecutor] [START] Starting plan '{self.plan_id}' "
            f"- goal: {self.graph.goal[:80]}"
        )
        self._persist()
        
        # Phase 21.5: Execution Safety Hook
        confidence = getattr(self.graph, "confidence", 1.0)
        CONFIDENCE_THRESHOLD = 0.6
        if confidence < CONFIDENCE_THRESHOLD and self.session:
            print(f"[ReActExecutor] [WARN] Plan confidence ({confidence:.2f}) is below threshold ({CONFIDENCE_THRESHOLD}). Requesting user approval...")
            from agentx.runtime.event_bus import bus, EVENTS
            self.session.interrupt()
            bus.publish(EVENTS.get("AWAITING_APPROVAL", "AWAITING_APPROVAL"), {"type": "plan", "graph": self.graph})
            import time
            while self.session.is_interrupted:
                time.sleep(1)
            # Check if plan execution was rejected
            if getattr(self.session, "is_rejected", False):
                print("[ReActExecutor] [FAIL] Plan execution rejected by user.")
                return False

        wave_count = 0
        scheduler = Scheduler(self.graph)

        from agentx.planning.verifier import verify_step
        from agentx.runtime.event_bus import bus, EVENTS
        
        MAX_REPAIR_ATTEMPTS = 2
        RISK_THRESHOLD = 0.8
        
        while True:
            # 3.2 Interrupt Hook
            if self.session and self.session.is_interrupted:
                print(f"[ReActExecutor] Execution interrupted for plan '{self.plan_id}'. Pausing...")
                import time
                while self.session.is_interrupted:
                    time.sleep(1)
                print(f"[ReActExecutor] Execution resumed for plan '{self.plan_id}'.")
                
            ready_nodes = scheduler.ready_nodes()
            if not ready_nodes:
                break

            if wave_count >= MAX_WAVES:
                print(f"[ReActExecutor] [WARN] Max wave limit ({MAX_WAVES}) reached - aborting.")
                break

            wave_count += 1
            print(f"[ReActExecutor] Wave {wave_count}: {[n.id for n in ready_nodes]}")

            safe_batches = self._split_into_safe_batches(ready_nodes)

            for batch_idx, batch in enumerate(safe_batches):
                if len(safe_batches) > 1:
                    print(f"[ReActExecutor]   Sub-batch {batch_idx+1}/{len(safe_batches)}: {[n.id for n in batch]}")

                has_escalated = False
                
                # Execute sequentially for safe transactional boundary
                for node in batch:
                    # 1. Checkpoint state
                    self._bridge.checkpoint_state(node.id)
                    
                    # 1.5 Permission Check
                    from agentx.security.permissions import default_permissions
                    if not default_permissions.allow(getattr(node, 'tool', 'agent.coder')):
                        node.status = "FAILED"
                        node.error = f"PermissionError: {getattr(node, 'tool', 'agent.coder')} is not allowed by current security policies."
                        has_escalated = True
                        break

                    # OITL Control: Risk Gate
                    if getattr(node, 'risk', 0.0) >= RISK_THRESHOLD and self.session:
                        print(f"[ReActExecutor] Risk threshold exceeded for node '{node.id}'. Requesting user approval...")
                        node.status = "AWAITING_APPROVAL"
                        self.session.pending_node = node
                        bus.publish(EVENTS.get("AWAITING_APPROVAL", "AWAITING_APPROVAL"), node)
                        self.session.interrupt()
                        import time
                        while self.session.is_interrupted:
                            time.sleep(1)
                        self.session.pending_node = None
                        # Assume modified or approved. If rejected, it would be set to FAILED via API.
                        if node.status == "FAILED":
                            has_escalated = True
                            break
                        node.status = "RUNNING"


                    # 2. Verify Step
                    v = verify_step(node, self._bridge.system_state)
                    if not v.get("safe", True):
                        print(f"[ReActExecutor] [WARN] Pre-execution verification failed for '{node.id}': {v.get('missing')}")
                        
                        if getattr(node, 'repair_attempts', 0) >= MAX_REPAIR_ATTEMPTS:
                            node.status = "FAILED"
                            node.error = "Max repair attempts reached."
                            has_escalated = True
                            break
                        
                        repaired = self._replanner.repair_subtree(node, self._bridge.system_state)
                        if repaired:
                            node.repair_attempts = getattr(node, 'repair_attempts', 0) + 1
                            v = VersionStore.cut(self.plan_id, self.graph, parent_id=self.current_version_id, label="repair")
                            self.current_version_id = v.id
                            print(f"[ReActExecutor] Version cut: {v.id[:8]}... (pre-exec repair on '{node.id}')")
                            # Break the batch, rebuild scheduler next iteration
                            break
                        else:
                            node.status = "FAILED"
                            node.error = "Pre-execution verification failed, and repair failed."
                            
                    # 3. Execute
                    if node.status != "FAILED":
                        success = self._bridge.run_node(node)
                    else:
                        success = False
                        
                    # 4. Handle Failure, Rollback & Compensation
                    if not success:
                        self._bridge.rollback_to(node.id)
                        
                        # Forward recovery: Compensation
                        policy = getattr(node, 'policy', None)
                        if policy and getattr(policy, 'compensation', None):
                            print(f"[ReActExecutor] Triggering compensation for '{node.id}': {policy.compensation}")
                            from agentx.capabilities.registry import registry
                            try:
                                # Assuming compensation is a terminal command string for now
                                comp_cap = registry.get("terminal.exec")
                                comp_cap.execute({"cmd": policy.compensation})
                            except Exception as e:
                                print(f"[ReActExecutor] [WARN] Compensation failed: {e}")
                        
                        from agentx.planning.failure_classifier import classify_failure, FailureType
                        f_type = classify_failure(node.error)
                        print(f"[ReActExecutor] Failure classified as: {f_type.upper()}")
                        
                        # Handle TRANSIENT failures (Retry based on policy)
                        if f_type == FailureType.TRANSIENT and policy and getattr(node, 'attempt', 1) <= getattr(policy, 'retry', 0):
                            print(f"[ReActExecutor] Retrying transient failure (Attempt {node.attempt}/{policy.retry})")
                            node.status = "PENDING"  # Will be picked up next schedule
                            continue

                        # Handle LOGIC or EXTERNAL failures
                        from agentx.planning.failure_memory import FailureMemory
                        from agentx.embeddings.service import EmbeddingService
                        embed = EmbeddingService().embed
                        
                        FailureMemory.record({
                            "goal": self.graph.goal,
                            "node": node.id,
                            "state": self._bridge.system_state,
                            "plan_embedding": embed(self.graph.to_json()),
                            "error": node.error,
                            "failure_type": f_type
                        })
                        
                        if getattr(node, 'repair_attempts', 0) >= MAX_REPAIR_ATTEMPTS or f_type == FailureType.EXTERNAL:
                            action = self._replanner.handle_failure(node)
                            self._record_repair(node, action)
                            if action == RecoveryAction.ESCALATE:
                                has_escalated = True
                            break
                        
                        # Try to repair LOGIC failures
                        if f_type == FailureType.LOGIC:
                            repaired = self._replanner.repair_subtree(node, self._bridge.system_state)
                            if not repaired:
                                # Fallback to standard replanner logic (escalate/retry)
                                action = self._replanner.handle_failure(node)
                                self._record_repair(node, action)
                                if action == RecoveryAction.ESCALATE:
                                    has_escalated = True
                            else:
                                node.repair_attempts = getattr(node, 'repair_attempts', 0) + 1
                                v = VersionStore.cut(self.plan_id, self.graph, parent_id=self.current_version_id, label="repair")
                                self.current_version_id = v.id
                                print(f"[ReActExecutor] Version cut: {v.id[:8]}... (post-failure repair on '{node.id}')")
                        break # Break current batch to pick up repaired nodes in next scheduler loop
                
                if has_escalated:
                    print(f"[ReActExecutor] [FAIL] Node escalated - stopping wave sequence.")
                    self._persist()
                    return False

            # Persist after every wave
            self._persist()

            # Rebuild scheduler to pick up any nodes injected by repair or decompose
            scheduler = Scheduler(self.graph)

        success = self._all_completed()
        status_icon = "[OK]" if success else "[FAIL]"
        print(
            f"[ReActExecutor] {status_icon} Plan '{self.plan_id}' finished "
            f"- success={success}, waves={wave_count}"
        )
        self._persist()
        self._update_or_learn_method(success)
        return success

    # -- helpers ------------------------------------------------------------

    def _all_completed(self) -> bool:
        return all(
            n.status == "COMPLETED"
            for n in self.graph.nodes
        )

    def _persist(self) -> None:
        try:
            PlanStore.save(self.plan_id, self.graph)
        except Exception as exc:
            print(f"[ReActExecutor] PlanStore.save() warning: {exc}")

    def _record_repair(self, node: PlanNode, action: RecoveryAction) -> None:
        # Find the latest RepairRecord for this node
        for rec in reversed(self.repair_history):
            if rec.node_id == node.id:
                try:
                    PlanStore.record_repair(
                        plan_id=self.plan_id,
                        node_id=rec.node_id,
                        attempt=rec.attempt,
                        failure_kind=rec.failure_kind,
                        action_taken=rec.action_taken,
                        notes=rec.notes,
                    )
                except Exception as exc:
                    print(f"[ReActExecutor] PlanStore.record_repair() warning: {exc}")
                break

    def summary(self) -> dict:
        """Return a readable execution summary."""
        counts = {"COMPLETED": 0, "FAILED": 0, "PENDING": 0, "RUNNING": 0}
        for n in self.graph.nodes:
            counts[n.status] = counts.get(n.status, 0) + 1
        return {
            "plan_id": self.plan_id,
            "goal": self.graph.goal,
            "total_nodes": len(self.graph.nodes),
            "node_counts": counts,
            "repairs": len(self.repair_history),
        }

    def _update_or_learn_method(self, success: bool) -> None:
        """
        Phase 12 post-execution hook.

        If the plan came from a cached method (tagged with _source_method_id),
        update that method's metrics.  Otherwise, if the plan was LLM-generated
        and successful, attempt to learn a new method from it.

        This method is intentionally wrapped in a broad try/except so that
        any learning failure never affects the final execution result.
        """
        primitives = self.graph.primitive_nodes()
        avg_uncertainty = (
            sum(n.uncertainty for n in primitives) / max(len(primitives), 1)
        )
        plan_score = max(0.0, 1.0 - avg_uncertainty) if success else 0.0

        source_method_id = getattr(self.graph, "_source_method_id", None)

        if source_method_id:
            # Update metrics for the method that generated this plan
            try:
                from agentx.planning.method_store import MethodStore
                from agentx.planning.method_scorer import update_metrics

                method = MethodStore.get_by_id(source_method_id)
                if method:
                    updated = update_metrics(
                        method,
                        success=success,
                        latency=0.0,
                        uncertainty=avg_uncertainty,
                    )
                    MethodStore.upsert(updated)
                    print(
                        f"[ReActExecutor] Updated metrics for method '{source_method_id}' "
                        f"(success={success}, score={updated.get('score', 0):.3f})"
                    )
            except Exception as exc:
                print(f"[ReActExecutor] Method metrics update failed (non-critical): {exc}")
        else:
            # LLM-generated plan: try to learn a new method
            try:
                from agentx.planning.method_learner import learn_method

                stored = learn_method(
                    self.graph,
                    goal=self.graph.goal,
                    success=success,
                    score=plan_score,
                )
                if stored:
                    print(f"[ReActExecutor] Learned new method from plan '{self.plan_id}'")
            except Exception as exc:
                print(f"[ReActExecutor] Method learning failed (non-critical): {exc}")
                
        # Phase 14 Wave 4: Record Failure to prevent future looping
        if not success:
            try:
                from agentx.planning.failure_memory import FailureMemory
                from agentx.embeddings.service import EmbeddingService
                embed = EmbeddingService().embed_text
                
                FailureMemory.record({
                    "goal": self.graph.goal,
                    "node": "ESCALATION",
                    "state": self._bridge.system_state,
                    "plan_embedding": embed(self.graph.summary()),
                    "plan_node_ids": [n.id for n in self.graph.primitive_nodes()],
                    "error": "Execution failed or escalated"
                })
            except Exception as e:
                print(f"[ReActExecutor] Failed to record failure to memory: {e}")
