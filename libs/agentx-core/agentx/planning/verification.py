"""
agentx/planning/verification.py
==================================
Phase 11 - Serializability Verification Layer.

Provides utilities to prove that parallel execution of a PlanGraph is 
equivalent to a valid sequential execution. This is used for stress 
testing and auditing the HTN executor.
"""

from __future__ import annotations

import copy
import random
import time
from typing import Dict, Any, List, Optional
from unittest.mock import patch

from agentx.planning.models import PlanGraph, PlanNode
from agentx.planning.react_executor import ReActExecutor
from agentx.planning.execution_bridge import ExecutionBridge
from agentx.planning.scheduler import Scheduler
from agentx.planning.replanner import RecoveryAction


class SerializabilityVerifier:
    """
    Compares sequential and parallel execution outcomes for a PlanGraph.
    
    Equivalence is defined as:
    1. Final system_state is identical.
    2. Set of COMPLETED vs FAILED nodes is identical.
    """

    def __init__(self, graph: PlanGraph):
        self.original_graph = graph

    def _mock_env(self, randomize_timing: bool = False):
        """
        Setup mocks for engine calls, persistence, and replanning.
        """
        def mocked_cmd_run(*args, **kwargs):
            if randomize_timing:
                # Add jitter to expose race conditions in state updates
                time.sleep(random.uniform(0.0001, 0.001))
            return None
        
        def mocked_handle_failure(node):
            # For verification, we want deterministic failure handling.
            # We don't want to decompose/retry unless requested.
            # We'll just escalate to stop the wave if a conflict is missed.
            node.status = "FAILED"
            return RecoveryAction.ESCALATE

        return [
            patch('agentx.planning.execution_bridge._cmd_run', side_effect=mocked_cmd_run),
            patch('agentx.planning.plan_store.PlanStore.save'),
            patch('agentx.planning.plan_store.PlanStore.record_repair'),
            patch('agentx.planning.replanner.Replanner.handle_failure', side_effect=mocked_handle_failure),
        ]

    def run_sequential(self) -> Dict[str, Any]:
        """
        Execute nodes wave-by-wave, but strictly one-at-a-time within 
        each wave in deterministic (ID-based) order.
        """
        graph = copy.deepcopy(self.original_graph)
        bridge = ExecutionBridge(graph)
        
        mocks = self._mock_env()
        with mocks[0], mocks[1], mocks[2], mocks[3]:
            scheduler = Scheduler(graph)
            while True:
                ready_nodes = scheduler.ready_nodes()
                if not ready_nodes:
                    break
                
                # Deterministic sequential order
                has_failure = False
                for node in sorted(ready_nodes, key=lambda n: n.id):
                    success = bridge.run_node(node)
                    if not success:
                        has_failure = True
                        break # Stop this wave
                
                if has_failure:
                    break # Stop execution sequence
                
                scheduler = Scheduler(graph)
        
        return {
            "state": bridge.system_state,
            "status": {n.id: n.status for n in graph.nodes}
        }

    def run_parallel(self, randomize_timing: bool = False) -> Dict[str, Any]:
        """
        Execute nodes using the standard ReActExecutor (Parallel).
        """
        graph = copy.deepcopy(self.original_graph)
        executor = ReActExecutor(graph)
        
        mocks = self._mock_env(randomize_timing=randomize_timing)
        with mocks[0], mocks[1], mocks[2], mocks[3]:
            executor.run()
        
        return {
            "state": executor._bridge.system_state,
            "status": {n.id: n.status for n in graph.nodes}
        }

    def verify(self, iterations: int = 1, jitter: bool = True) -> bool:
        """
        Performs sequential and parallel runs and asserts equivalence.
        """
        sequential_results = self.run_sequential()
        
        for i in range(iterations):
            parallel_results = self.run_parallel(randomize_timing=jitter)
            
            # 1. State check
            if sequential_results["state"] != parallel_results["state"]:
                print(f"[Verifier] [FAIL] State mismatch on iteration {i}")
                print(f"  Seq: {sequential_results['state']}")
                print(f"  Par: {parallel_results['state']}")
                return False
            
            # 2. Status check
            if sequential_results["status"] != parallel_results["status"]:
                print(f"[Verifier] [FAIL] Status mismatch on iteration {i}")
                print(f"  Seq: {sequential_results['status']}")
                print(f"  Par: {parallel_results['status']}")
                return False
                
        return True
