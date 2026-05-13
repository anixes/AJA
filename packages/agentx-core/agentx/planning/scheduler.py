"""
agentx/planning/scheduler.py
=============================
Phase 11 - Dependency-aware Scheduler.

Produces an ordered execution sequence from a validated PlanGraph using
Kahn's topological sort.  Within the same "wave" (nodes whose dependencies
are all satisfied), ordering is deterministic (sorted by node id) so tests
are reproducible.

Key concepts
------------
* **Wave** - a batch of nodes that can run in parallel (all dependencies met).
  The ReActExecutor can choose to run a wave serially or concurrently.
* The scheduler is stateless and re-entrant; it operates on the *current*
  status snapshot of the graph, so it can be called again after partial
  execution (for replanning / retry scenarios).

Usage
-----
    scheduler = Scheduler(graph)
    for wave in scheduler.waves():
        for node in wave:
            execute(node)
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterator, List

from agentx.planning.models import PlanGraph, PlanNode


class Scheduler:
    """
    Dependency-based topological scheduler for PlanGraph.

    Parameters
    ----------
    graph : PlanGraph
        Must be a validated DAG (call DAGValidator.validate first).
    """

    def __init__(self, graph: PlanGraph):
        self.graph = graph

    # -- public API ---------------------------------------------------------

    def waves(self) -> Iterator[List[PlanNode]]:
        """
        Yield successive waves of **primitive** PlanNodes in topological order.

        Compound nodes are structural organisers and are NEVER yielded for
        execution.  Their dependency edges are resolved transparently so that
        primitive children inherit the compound node's position in the DAG.

        Each wave contains primitive nodes whose dependencies have all been
        satisfied (COMPLETED status).  Nodes within a wave are sorted by id
        for deterministic ordering.

        Only PENDING and FAILED primitives are scheduled; COMPLETED / RUNNING
        nodes are treated as already-satisfied for dependency resolution.
        """
        node_map = {n.id: n for n in self.graph.nodes}
        in_degree: dict = defaultdict(int)
        adj: dict = defaultdict(list)  # predecessor - [successors]

        for node in self.graph.nodes:
            in_degree.setdefault(node.id, 0)
            for dep_id in node.dependencies:
                if dep_id in node_map:
                    adj[dep_id].append(node.id)
                    in_degree[node.id] += 1

        # Mark already-completed nodes as resolved
        for node in self.graph.nodes:
            if node.status in ("COMPLETED",):
                for successor_id in adj[node.id]:
                    in_degree[successor_id] -= 1

        # Seed the queue with ready nodes (in_degree == 0, need work)
        queue = deque()
        for node in self.graph.nodes:
            if node.status in ("PENDING", "FAILED") and in_degree[node.id] == 0:
                queue.append(node.id)

        while queue:
            wave_ids = sorted(queue)   # deterministic ordering
            queue.clear()

            # Only surface primitive nodes - skip compound organisers
            primitive_wave = [
                node_map[nid] for nid in wave_ids
                if nid in node_map and node_map[nid].is_primitive
            ]

            # Still need to unlock successors for compound nodes in this wave
            for nid in wave_ids:
                node = node_map.get(nid)
                if node and node.is_compound:
                    # Treat compound as instantly resolved for scheduling
                    for successor_id in adj[nid]:
                        in_degree[successor_id] -= 1
                        dep_node = node_map.get(successor_id)
                        if dep_node and dep_node.status in ("PENDING", "FAILED"):
                            if in_degree[successor_id] == 0:
                                queue.append(successor_id)

            if primitive_wave:
                yield primitive_wave

            # Unlock successors from the primitive wave
            for node in primitive_wave:
                for successor_id in adj[node.id]:
                    in_degree[successor_id] -= 1
                    dep_node = node_map.get(successor_id)
                    if dep_node and dep_node.status in ("PENDING", "FAILED"):
                        if in_degree[successor_id] == 0:
                            queue.append(successor_id)

    def primitive_waves(self) -> Iterator[List[PlanNode]]:
        """
        Canonical HTN entry-point - alias for waves().
        Explicitly communicates that only primitive nodes are scheduled.
        """
        return self.waves()

    def flat_order(self) -> List[PlanNode]:
        """
        Convenience: flatten all primitive waves into a single ordered list.
        Compound nodes are excluded - only executable primitives are returned.
        """
        result: List[PlanNode] = []
        for wave in self.waves():
            result.extend(wave)
        return result

    def ready_nodes(self) -> List[PlanNode]:
        """
        Return **primitive** nodes ready to execute RIGHT NOW based on the
        current `.status` of each node.  Compound nodes are never returned.
        Useful for the ReActExecutor polling loop.
        """
        completed_ids = {n.id for n in self.graph.nodes if n.status == "COMPLETED"}
        ready = []
        for node in self.graph.nodes:
            if not node.is_primitive:
                continue   # compound nodes are never executed
            if node.status not in ("PENDING", "FAILED"):
                continue
            if all(dep in completed_ids for dep in node.dependencies):
                ready.append(node)
        return sorted(ready, key=lambda n: n.id)
