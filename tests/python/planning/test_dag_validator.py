"""
tests/planning/test_dag_validator.py
======================================
Unit tests for DAGValidator — cycle detection, dependency integrity, etc.
"""

import pytest
from agentx.planning.models import DoD, PlanGraph, PlanNode
from agentx.planning.dag_validator import DAGValidator, UNCERTAINTY_HARD_LIMIT


def _node(nid, deps=None, uncertainty=0.1, inputs=None):
    return PlanNode(
        id=nid,
        task=f"task for {nid}",
        dependencies=deps or [],
        strategy="direct",
        inputs=inputs or [],
        outputs={},
        dod=DoD("ok", "deterministic"),
        uncertainty=uncertainty,
    )


def _graph(*nodes):
    return PlanGraph(goal="test", nodes=list(nodes))


class TestDAGValidatorEmpty:
    def test_empty_graph_fails(self):
        g = _graph()
        r = DAGValidator.validate(g)
        assert not r.ok
        assert any("no nodes" in e.lower() for e in r.errors)


class TestDAGValidatorDuplicateIds:
    def test_duplicate_ids(self):
        g = _graph(_node("a"), _node("a"))
        r = DAGValidator.validate(g)
        assert not r.ok
        assert any("duplicate" in e.lower() for e in r.errors)


class TestDAGValidatorMissingDeps:
    def test_unknown_dependency(self):
        g = _graph(_node("a", deps=["ghost"]))
        r = DAGValidator.validate(g)
        assert not r.ok
        assert any("ghost" in e for e in r.errors)

    def test_unknown_input(self):
        g = _graph(_node("a", inputs=["phantom"]))
        r = DAGValidator.validate(g)
        assert not r.ok
        assert any("phantom" in e for e in r.errors)


class TestDAGValidatorSelfLoop:
    def test_self_loop_detected(self):
        n = _node("a")
        n.dependencies = ["a"]   # bypass __post_init__ validation on PlanNode
        g = _graph(n)
        r = DAGValidator.validate(g)
        assert not r.ok
        assert any("self-loop" in e.lower() for e in r.errors)


class TestDAGValidatorCycles:
    def test_two_node_cycle(self):
        a = _node("a", deps=["b"])
        b = _node("b", deps=["a"])
        g = _graph(a, b)
        r = DAGValidator.validate(g)
        assert not r.ok
        assert any("cycle" in e.lower() for e in r.errors)

    def test_three_node_cycle(self):
        a = _node("a", deps=["c"])
        b = _node("b", deps=["a"])
        c = _node("c", deps=["b"])
        g = _graph(a, b, c)
        r = DAGValidator.validate(g)
        assert not r.ok
        assert any("cycle" in e.lower() for e in r.errors)

    def test_linear_dag_is_valid(self):
        a = _node("a")
        b = _node("b", deps=["a"])
        c = _node("c", deps=["b"])
        g = _graph(a, b, c)
        r = DAGValidator.validate(g)
        assert r.ok, r.errors

    def test_diamond_dag_is_valid(self):
        a = _node("a")
        b = _node("b", deps=["a"])
        c = _node("c", deps=["a"])
        d = _node("d", deps=["b", "c"])
        g = _graph(a, b, c, d)
        r = DAGValidator.validate(g)
        assert r.ok, r.errors


class TestDAGValidatorUncertainty:
    def test_high_uncertainty_flagged(self):
        n = _node("a", uncertainty=UNCERTAINTY_HARD_LIMIT + 0.01)
        # Manually override since PlanNode clamps only to 1.0
        n.uncertainty = UNCERTAINTY_HARD_LIMIT + 0.01
        g = _graph(n)
        r = DAGValidator.validate(g)
        assert not r.ok
        assert any("uncertainty" in e.lower() for e in r.errors)

    def test_boundary_uncertainty_ok(self):
        a = _node("a", uncertainty=UNCERTAINTY_HARD_LIMIT)
        g = _graph(a)
        r = DAGValidator.validate(g)
        # Exactly at limit is OK (strict >)
        assert r.ok, r.errors
