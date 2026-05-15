"""
tests/planning/test_scheduler.py
==================================
Unit tests for the dependency-aware Scheduler.
"""

import pytest
from agentx.planning.models import DoD, PlanGraph, PlanNode
from agentx.planning.scheduler import Scheduler


def _node(nid, deps=None, status="PENDING"):
    n = PlanNode(
        id=nid,
        task=f"task {nid}",
        dependencies=deps or [],
        strategy="direct",
        inputs=[],
        outputs={},
        dod=DoD("ok", "deterministic"),
        uncertainty=0.1,
    )
    n.status = status
    return n


def _graph(*nodes):
    return PlanGraph(goal="test", nodes=list(nodes))


class TestSchedulerLinear:
    def test_linear_order(self):
        g = _graph(_node("a"), _node("b", deps=["a"]), _node("c", deps=["b"]))
        s = Scheduler(g)
        order = s.flat_order()
        ids = [n.id for n in order]
        assert ids == ["a", "b", "c"]

    def test_three_waves_linear(self):
        g = _graph(_node("a"), _node("b", deps=["a"]), _node("c", deps=["b"]))
        s = Scheduler(g)
        waves = list(s.waves())
        assert len(waves) == 3
        assert [n.id for n in waves[0]] == ["a"]
        assert [n.id for n in waves[1]] == ["b"]
        assert [n.id for n in waves[2]] == ["c"]


class TestSchedulerParallel:
    def test_independent_nodes_in_same_wave(self):
        g = _graph(_node("a"), _node("b"), _node("c"))
        s = Scheduler(g)
        waves = list(s.waves())
        assert len(waves) == 1
        assert sorted(n.id for n in waves[0]) == ["a", "b", "c"]

    def test_diamond_waves(self):
        a = _node("a")
        b = _node("b", deps=["a"])
        c = _node("c", deps=["a"])
        d = _node("d", deps=["b", "c"])
        g = _graph(a, b, c, d)
        s = Scheduler(g)
        waves = list(s.waves())
        assert len(waves) == 3
        assert [n.id for n in waves[0]] == ["a"]
        assert sorted(n.id for n in waves[1]) == ["b", "c"]
        assert [n.id for n in waves[2]] == ["d"]


class TestSchedulerWithCompletedNodes:
    def test_already_completed_skipped(self):
        a = _node("a", status="COMPLETED")
        b = _node("b", deps=["a"])
        g = _graph(a, b)
        s = Scheduler(g)
        order = s.flat_order()
        # Only 'b' should be scheduled (a is already done)
        assert [n.id for n in order] == ["b"]

    def test_ready_nodes_respects_completed(self):
        a = _node("a", status="COMPLETED")
        b = _node("b", deps=["a"])
        c = _node("c", deps=["b"])
        g = _graph(a, b, c)
        ready = Scheduler(g).ready_nodes()
        assert [n.id for n in ready] == ["b"]


class TestSchedulerSingleNode:
    def test_single_node(self):
        g = _graph(_node("only"))
        s = Scheduler(g)
        assert s.flat_order()[0].id == "only"

    def test_single_completed_node(self):
        g = _graph(_node("only", status="COMPLETED"))
        s = Scheduler(g)
        assert s.flat_order() == []
