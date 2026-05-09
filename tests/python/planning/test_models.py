"""
tests/planning/test_models.py
==============================
Unit tests for PlanNode, PlanGraph, and DoD data model.
"""

import json
import pytest
from agentx.planning.models import DoD, PlanNode, PlanGraph, VALID_STRATEGIES


# ---------------------------------------------------------------------------
# DoD
# ---------------------------------------------------------------------------

class TestDoD:
    def test_valid_creation(self):
        d = DoD("task must succeed", "deterministic")
        assert d.success_criteria == "task must succeed"
        assert d.validation_type == "deterministic"

    def test_invalid_validation_type(self):
        with pytest.raises(ValueError, match="validation_type"):
            DoD("ok", "magic")

    def test_roundtrip(self):
        d = DoD("check output", "semantic")
        assert DoD.from_dict(d.to_dict()) == d


# ---------------------------------------------------------------------------
# PlanNode
# ---------------------------------------------------------------------------

class TestPlanNode:
    def _make(self, **kwargs):
        defaults = dict(
            id="node_a",
            task="do something",
            dependencies=[],
            strategy="direct",
            inputs=[],
            outputs={},
            dod=DoD("ok", "deterministic"),
            uncertainty=0.2,
        )
        defaults.update(kwargs)
        return PlanNode(**defaults)

    def test_valid_node(self):
        n = self._make()
        assert n.id == "node_a"
        assert n.status == "PENDING"

    def test_empty_id_raises(self):
        with pytest.raises(ValueError, match="id"):
            self._make(id="")

    def test_empty_task_raises(self):
        with pytest.raises(ValueError, match="task"):
            self._make(task="")

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValueError, match="strategy"):
            self._make(strategy="teleport")

    def test_uncertainty_out_of_range(self):
        with pytest.raises(ValueError, match="uncertainty"):
            self._make(uncertainty=1.5)

    def test_serialise_roundtrip(self):
        n = self._make(outputs={"result": "some data"})
        d = n.to_dict()
        n2 = PlanNode.from_dict(d)
        assert n2.id == n.id
        assert n2.task == n.task
        assert n2.outputs == n.outputs
        assert n2.uncertainty == n.uncertainty

    @pytest.mark.parametrize("strategy", VALID_STRATEGIES)
    def test_all_strategies_accepted(self, strategy):
        n = self._make(strategy=strategy)
        assert n.strategy == strategy


# ---------------------------------------------------------------------------
# PlanGraph
# ---------------------------------------------------------------------------

class TestPlanGraph:
    def _graph(self, num_nodes=3):
        nodes = []
        for i in range(num_nodes):
            nodes.append(PlanNode(
                id=f"node_{i}",
                task=f"task {i}",
                dependencies=[f"node_{i-1}"] if i > 0 else [],
                strategy="direct",
                inputs=[f"node_{i-1}"] if i > 0 else [],
                outputs={"out": "data"},
                dod=DoD("ok", "deterministic"),
                uncertainty=0.1,
            ))
        return PlanGraph(goal="test goal", nodes=nodes)

    def test_basic_graph(self):
        g = self._graph(3)
        assert len(g.nodes) == 3

    def test_root_nodes(self):
        g = self._graph(3)
        roots = g.root_nodes()
        assert len(roots) == 1
        assert roots[0].id == "node_0"

    def test_node_by_id(self):
        g = self._graph(2)
        n = g.node_by_id("node_1")
        assert n is not None
        assert n.task == "task 1"

    def test_node_by_id_missing(self):
        g = self._graph(2)
        assert g.node_by_id("ghost") is None

    def test_json_roundtrip(self):
        g = self._graph(2)
        raw = g.to_json()
        g2 = PlanGraph.from_json(raw)
        assert g2.goal == g.goal
        assert len(g2.nodes) == len(g.nodes)
        assert g2.nodes[0].id == g.nodes[0].id

    def test_dict_roundtrip(self):
        g = self._graph(1)
        g2 = PlanGraph.from_dict(g.to_dict())
        assert g2.goal == g.goal
