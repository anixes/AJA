"""
tests/planning/test_method_system.py
======================================
Phase 12 - Self-Improving Method System Test Suite.

11 tests covering: scoring, metric updates, retrieval, fit scoring,
learning, deduplication, and pruning.  All tests run without LLM or
network access.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import pytest

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

def _make_method(
    mid="test_method",
    pattern="deploy a web service",
    task_type="deploy",
    success_rate=1.0,
    avg_uncertainty=0.1,
    avg_latency=0.0,
    reuse_count=5,
    stability=1.0,
    score=None,
    nodes=None,
):
    """Create a minimal Phase 12 method dict."""
    if nodes is None:
        nodes = [
            {"id": "step1", "task": "prepare", "dependencies": [], "preconditions": {}, "effects": {"prepared": True},
             "type": "primitive", "children": [], "strategy": "direct", "inputs": [], "outputs": {},
             "dod": {"success_criteria": "done", "validation_type": "deterministic"}, "uncertainty": 0.1},
            {"id": "step2", "task": "execute", "dependencies": ["step1"], "preconditions": {"prepared": True},
             "effects": {"deployed": True}, "type": "primitive", "children": [], "strategy": "direct",
             "inputs": [], "outputs": {},
             "dod": {"success_criteria": "done", "validation_type": "deterministic"}, "uncertainty": 0.1},
        ]
    method = {
        "id": mid,
        "task_type": task_type,
        "pattern": pattern,
        "plan_template": {"goal": pattern, "nodes": nodes},
        "metrics": {
            "success_rate": success_rate,
            "avg_uncertainty": avg_uncertainty,
            "avg_latency": avg_latency,
            "reuse_count": reuse_count,
            "stability": stability,
        },
        "score": score if score is not None else 0.5,
        "last_used": None,
    }
    return method


def _make_plan_graph(goal="deploy a web service", success_rate_uncertainty=0.1):
    """Create a minimal PlanGraph with 2 primitive nodes."""
    from agentx.planning.models import PlanGraph, PlanNode, DoD
    n1 = PlanNode(
        id="step1", task="prepare",
        effects={"prepared": True},
        dod=DoD("done", "deterministic"),
        uncertainty=success_rate_uncertainty,
        node_type="primitive",
    )
    n2 = PlanNode(
        id="step2", task="execute",
        dependencies=["step1"],
        preconditions={"prepared": True},
        effects={"deployed": True},
        dod=DoD("done", "deterministic"),
        uncertainty=success_rate_uncertainty,
        node_type="primitive",
    )
    n1.status = "COMPLETED"
    n2.status = "COMPLETED"
    return PlanGraph(goal=goal, nodes=[n1, n2])


@pytest.fixture(autouse=True)
def isolated_method_store(tmp_path, monkeypatch):
    """Redirect MethodStore file path to a temp dir for each test."""
    methods_path = str(tmp_path / "methods.json")
    monkeypatch.setattr(
        "agentx.planning.method_store.METHODS_FILE", methods_path
    )
    yield methods_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScoreMethod:
    def test_score_method_baseline(self):
        """Perfect method (success=1, uncertainty=0, many reuses) should score > 0.7."""
        from agentx.planning.method_scorer import score_method
        m = _make_method(success_rate=1.0, avg_uncertainty=0.0, reuse_count=50, stability=1.0)
        score = score_method(m)
        assert score > 0.7, f"Expected score > 0.7, got {score}"

    def test_score_method_bad(self):
        """Failed method (success=0, uncertainty=0.9) should score < 0.3."""
        from agentx.planning.method_scorer import score_method
        m = _make_method(success_rate=0.0, avg_uncertainty=0.9, reuse_count=0, stability=0.0)
        score = score_method(m)
        assert score < 0.3, f"Expected score < 0.3, got {score}"

    def test_score_missing_metrics(self):
        """Method with no metrics dict returns neutral 0.4."""
        from agentx.planning.method_scorer import score_method
        m = {"id": "x", "plan_template": {}}
        assert score_method(m) == pytest.approx(0.4)


class TestUpdateMetrics:
    def test_update_metrics_success(self):
        """After a success, reuse_count increases and success_rate stays high."""
        from agentx.planning.method_scorer import update_metrics
        m = _make_method(success_rate=1.0, reuse_count=5, stability=0.9)
        updated = update_metrics(m, success=True, latency=1.0, uncertainty=0.1)
        assert updated["metrics"]["reuse_count"] == 6
        assert updated["metrics"]["success_rate"] > 0.9
        assert updated["last_used"] is not None

    def test_update_metrics_failure(self):
        """After a failure, stability decreases by ~0.10."""
        from agentx.planning.method_scorer import update_metrics
        m = _make_method(stability=0.8, reuse_count=3)
        before_stability = m["metrics"]["stability"]
        updated = update_metrics(m, success=False, latency=5.0, uncertainty=0.7)
        assert updated["metrics"]["stability"] < before_stability
        # stability should drop by ~0.10
        assert updated["metrics"]["stability"] == pytest.approx(before_stability - 0.10, abs=0.001)

    def test_score_recomputed_after_update(self):
        """Score is recomputed after update_metrics call."""
        from agentx.planning.method_scorer import update_metrics, score_method
        m = _make_method(success_rate=0.5, reuse_count=1)
        original_score = m["score"]
        # Force score to stale value
        m["score"] = 0.0
        updated = update_metrics(m, success=True, latency=0.5, uncertainty=0.2)
        assert updated["score"] != 0.0  # must have been recomputed
        assert updated["score"] > 0.0


class TestRetrieveMethods:
    def _seed_store(self, methods_path, methods):
        with open(methods_path, "w") as f:
            json.dump(methods, f)

    def test_retrieve_top_n(self, isolated_method_store):
        """Correct top method is returned from 3 candidates."""
        from agentx.planning.method_scorer import score_method
        from agentx.planning.method_retriever import retrieve_methods

        m1 = _make_method("m1", "deploy a kubernetes service", score=0.8)
        m2 = _make_method("m2", "bake a chocolate cake", score=0.9)
        m3 = _make_method("m3", "deploy a web service to production", score=0.7)
        for m in [m1, m2, m3]:
            m["score"] = score_method(m)
        self._seed_store(isolated_method_store, [m1, m2, m3])

        results = retrieve_methods("deploy a production service", top_n=3)
        # m1 and m3 should rank above m2 (cake is unrelated)
        assert len(results) >= 1
        ids = [m["id"] for m, sim in results]
        assert "m2" not in ids or ids.index("m2") > 0  # cake should not be first

    def test_retrieve_returns_empty_on_no_match(self, isolated_method_store):
        """Returns empty list when no method matches above similarity threshold."""
        from agentx.planning.method_retriever import retrieve_methods

        m = _make_method("m1", "bake a chocolate cake")
        self._seed_store(isolated_method_store, [m])

        results = retrieve_methods("quantum physics simulation", top_n=5)
        assert results == []

    def test_method_fit_score(self, isolated_method_store):
        """method_fit returns higher score for semantically matching method."""
        from agentx.planning.method_retriever import method_fit

        m_deploy = _make_method("m1", "deploy a web service", success_rate=0.9)
        m_unrelated = _make_method("m2", "bake a chocolate cake", success_rate=0.9)

        fit_deploy = method_fit(m_deploy, 0.9, current_state={})
        fit_unrelated = method_fit(m_unrelated, 0.2, current_state={})
        assert fit_deploy > fit_unrelated


class TestMethodLearner:
    def test_learn_method_stores(self, isolated_method_store):
        """Eligible plan creates an entry in MethodStore."""
        from agentx.planning.method_store import MethodStore
        from agentx.planning.method_learner import learn_method

        plan = _make_plan_graph(goal="deploy a web service", success_rate_uncertainty=0.1)
        result = learn_method(plan, goal="deploy a web service", success=True, score=0.80)

        assert result is True
        methods = MethodStore.load()
        assert len(methods) == 1
        assert methods[0]["pattern"] == "deploy a web service"

    def test_learn_method_dedup(self, isolated_method_store):
        """Second call with same-pattern goal merges, not duplicates."""
        from agentx.planning.method_store import MethodStore
        from agentx.planning.method_learner import learn_method

        plan1 = _make_plan_graph("deploy a web service")
        plan2 = _make_plan_graph("deploy a web service")

        learn_method(plan1, "deploy a web service", success=True, score=0.80)
        learn_method(plan2, "deploy a web service", success=True, score=0.85)

        methods = MethodStore.load()
        # Similar patterns should merge into 1 entry, not 2
        assert len(methods) == 1

    def test_learn_method_ineligible_failed(self, isolated_method_store):
        """Failed plan does NOT create a method entry."""
        from agentx.planning.method_store import MethodStore
        from agentx.planning.method_learner import learn_method

        plan = _make_plan_graph("deploy a web service")
        result = learn_method(plan, goal="deploy a web service", success=False, score=0.0)

        assert result is False
        assert MethodStore.load() == []

    def test_learn_method_ineligible_low_score(self, isolated_method_store):
        """Plan with score below LEARNING_THRESHOLD is not stored."""
        from agentx.planning.method_store import MethodStore
        from agentx.planning.method_learner import learn_method, LEARNING_THRESHOLD

        plan = _make_plan_graph("deploy a web service")
        result = learn_method(plan, goal="deploy a web service", success=True, score=LEARNING_THRESHOLD - 0.01)

        assert result is False
        assert MethodStore.load() == []


class TestMethodPruner:
    def _seed(self, isolated_method_store, methods):
        with open(isolated_method_store, "w") as f:
            json.dump(methods, f)

    def test_prune_low_score(self, isolated_method_store):
        """Method with score < 0.2 and reuse_count > 2 is pruned."""
        from agentx.planning.method_pruner import prune_methods

        bad = _make_method("bad", score=0.10)
        bad["metrics"]["reuse_count"] = 5
        bad["score"] = 0.10
        good = _make_method("good", score=0.75)
        self._seed(isolated_method_store, [bad, good])

        removed = prune_methods(min_score=0.2)
        assert removed == 1

        from agentx.planning.method_store import MethodStore
        remaining = MethodStore.load()
        assert len(remaining) == 1
        assert remaining[0]["id"] == "good"

    def test_prune_dedup_identical_patterns(self, isolated_method_store):
        """Two methods with identical patterns are merged into one."""
        from agentx.planning.method_pruner import prune_methods

        m1 = _make_method("m1", pattern="deploy a web service to production", score=0.8)
        m1["metrics"]["reuse_count"] = 10
        m2 = _make_method("m2", pattern="deploy a web service to production", score=0.6)
        m2["metrics"]["reuse_count"] = 5
        self._seed(isolated_method_store, [m1, m2])

        removed = prune_methods(similarity_threshold=0.99)
        assert removed == 1

        from agentx.planning.method_store import MethodStore
        remaining = MethodStore.load()
        assert len(remaining) == 1
        # Winner should be m1 (higher score)
        assert remaining[0]["id"] == "m1"
        # Reuse count should be merged
        assert remaining[0]["metrics"]["reuse_count"] == 15
