import pytest

from agentx.planning.models import PlanGraph, PlanNode, DoD
from agentx.planning.dag_validator import DAGValidator, ValidationResult
from agentx.planning.scheduler import Scheduler
from agentx.planning.execution_bridge import ExecutionBridge


def _make_node(id_str: str, node_type="primitive", children=None, deps=None) -> PlanNode:
    return PlanNode(
        id=id_str,
        task=f"Task {id_str}",
        node_type=node_type,
        children=children or [],
        dependencies=deps or [],
        dod=DoD("done", "deterministic"),
        uncertainty=0.1
    )


class TestHTNModels:
    def test_primitive_and_compound_properties(self):
        p = _make_node("p", "primitive")
        c = _make_node("c", "compound", ["p"])
        assert p.is_primitive
        assert not p.is_compound
        assert c.is_compound
        assert not c.is_primitive

    def test_graph_helpers(self):
        p1 = _make_node("p1")
        p2 = _make_node("p2")
        c1 = _make_node("c1", "compound", ["p1", "p2"])
        p3 = _make_node("p3") # independent primitive
        graph = PlanGraph(goal="HTN Test", nodes=[p1, p2, c1, p3])

        primitives = graph.primitive_nodes()
        compounds = graph.compound_nodes()
        leaf_prims = graph.leaf_primitives()
        c1_children = graph.children_of("c1")

        assert len(primitives) == 3
        assert len(compounds) == 1
        assert "c1" in [n.id for n in compounds]

        # p1 and p2 are children of c1. p3 is a leaf primitive.
        assert len(leaf_prims) == 1
        assert leaf_prims[0].id == "p3"

        assert len(c1_children) == 2
        assert "p1" in [n.id for n in c1_children]
        assert "p2" in [n.id for n in c1_children]


class TestHTNValidator:
    def test_valid_htn(self):
        graph = PlanGraph("valid", nodes=[
            _make_node("p1"),
            _make_node("p2"),
            _make_node("c1", "compound", ["p1", "p2"])
        ])
        res = DAGValidator.validate(graph)
        assert res.ok

    def test_compound_without_children_fails(self):
        graph = PlanGraph("fail", nodes=[
            _make_node("c1", "compound", [])
        ])
        res = DAGValidator.validate(graph)
        assert not res.ok
        assert any("no children" in e for e in res.errors)

    def test_primitive_with_children_fails(self):
        graph = PlanGraph("fail", nodes=[
            _make_node("p1", "primitive", ["p2"]),
            _make_node("p2")
        ])
        res = DAGValidator.validate(graph)
        assert not res.ok
        assert any("must not declare children" in e for e in res.errors)

    def test_missing_child_fails(self):
        graph = PlanGraph("fail", nodes=[
            _make_node("c1", "compound", ["missing"])
        ])
        res = DAGValidator.validate(graph)
        assert not res.ok
        assert any("unknown child 'missing'" in e for e in res.errors)

    def test_dependency_on_compound_fails(self):
        graph = PlanGraph("fail", nodes=[
            _make_node("p1"),
            _make_node("c1", "compound", ["p1"]),
            _make_node("p2", deps=["c1"])
        ])
        res = DAGValidator.validate(graph)
        assert not res.ok
        assert any("depends on compound node" in e for e in res.errors)


class TestHTNScheduler:
    def test_scheduler_skips_compound_nodes(self):
        p1 = _make_node("p1")
        p2 = _make_node("p2", deps=["p1"])
        c1 = _make_node("c1", "compound", ["p1", "p2"], deps=[])
        # Even if c1 has no dependencies, it should NOT be yielded
        graph = PlanGraph("HTN", nodes=[p1, p2, c1])
        sched = Scheduler(graph)

        waves = list(sched.primitive_waves())
        # wave 1 should only be p1
        assert len(waves) == 2
        assert [n.id for n in waves[0]] == ["p1"]
        assert [n.id for n in waves[1]] == ["p2"]
        
        # Test ready_nodes
        ready = sched.ready_nodes()
        assert [n.id for n in ready] == ["p1"]


class TestHTNExecutionBridge:
    def test_execution_bridge_rejects_compound(self):
        graph = PlanGraph("HTN", nodes=[_make_node("c1", "compound", ["p1"])])
        bridge = ExecutionBridge(graph)
        
        # Manually trying to run compound should fail
        assert bridge.run_node(graph.nodes[0]) is False
        assert graph.nodes[0].status == "FAILED"
        assert "HTN Violation" in graph.nodes[0].error
