"""
tests/planning/test_replanner.py
==================================
Unit tests for the Replanner — failure classification and recovery actions.
"""

import pytest
from unittest.mock import patch, MagicMock
from agentx.planning.models import DoD, PlanGraph, PlanNode
from agentx.planning.replanner import (
    Replanner,
    classify_error,
    FailureKind,
    RecoveryAction,
    MAX_RETRIES,
)


def _node(nid="test_node", status="FAILED", error="generic error", attempt=1, task="do task"):
    n = PlanNode(
        id=nid,
        task=task,
        dependencies=[],
        strategy="skill",
        inputs=[],
        outputs={},
        dod=DoD("ok", "deterministic"),
        uncertainty=0.2,
    )
    n.status = status
    n.error = error
    n.attempt = attempt
    return n


def _graph(node):
    return PlanGraph(goal="test", nodes=[node])


# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------

class TestClassifyError:
    @pytest.mark.parametrize("error,expected_kind", [
        ("401 Unauthorized", FailureKind.AUTH_ERROR),
        ("403 forbidden", FailureKind.AUTH_ERROR),
        ("429 Too Many Requests", FailureKind.RATE_LIMIT),
        ("rate-limit exceeded", FailureKind.RATE_LIMIT),
        ("Tool not found: search_web", FailureKind.TOOL_NOT_FOUND),
        ("unknown skill requested", FailureKind.TOOL_NOT_FOUND),
        ("KeyError: 'context'", FailureKind.CONTEXT_MISSING),
        ("missing_input detected", FailureKind.CONTEXT_MISSING),
        ("Request timed out", FailureKind.TIMEOUT),
        ("deadline exceeded", FailureKind.TIMEOUT),
        ("some random crash", FailureKind.UNKNOWN),
    ])
    def test_classify(self, error, expected_kind):
        assert classify_error(error) == expected_kind


# ---------------------------------------------------------------------------
# Replanner.handle_failure
# ---------------------------------------------------------------------------

class TestReplannerRetry:
    def test_unknown_error_retries(self):
        n = _node(error="something went wrong", attempt=1)
        g = _graph(n)
        r = Replanner(g)
        action = r.handle_failure(n)
        assert action == RecoveryAction.RETRY
        assert n.status == "PENDING"

    def test_rate_limit_retries(self):
        n = _node(error="429 Too Many Requests", attempt=1)
        g = _graph(n)
        with patch("agentx.planning.replanner.time.sleep"):   # skip real sleep
            r = Replanner(g)
            action = r.handle_failure(n)
        assert action == RecoveryAction.RETRY
        assert n.status == "PENDING"

    def test_tool_not_found_downgrades_strategy(self):
        n = _node(error="Tool not found: my_skill", attempt=1)
        g = _graph(n)
        r = Replanner(g)
        r.handle_failure(n)
        assert n.strategy == "direct"
        assert n.status == "PENDING"


class TestReplannerEscalate:
    def test_auth_error_escalates_immediately(self):
        n = _node(error="401 Unauthorized", attempt=1)
        g = _graph(n)
        r = Replanner(g)
        action = r.handle_failure(n)
        assert action == RecoveryAction.ESCALATE
        assert n.status == "FAILED"

    def test_max_retries_exceeded_escalates(self):
        n = _node(error="generic", attempt=MAX_RETRIES)
        g = _graph(n)
        r = Replanner(g)
        action = r.handle_failure(n)
        assert action == RecoveryAction.ESCALATE


class TestReplannerDecompose:
    def test_long_timeout_decomposes(self):
        long_task = "Do the first thing. Do the second thing with more details. And a third part too."
        n = _node(error="Request timed out", attempt=1, task=long_task)
        g = _graph(n)
        r = Replanner(g)
        action = r.handle_failure(n)
        assert action == RecoveryAction.DECOMPOSE
        # Graph should now have 3 nodes (parent + 2 children)
        assert len(g.nodes) == 3
        child_ids = [nd.id for nd in g.nodes]
        assert f"{n.id}_part_a" in child_ids
        assert f"{n.id}_part_b" in child_ids

    def test_short_timeout_retries(self):
        short_task = "short task"
        n = _node(error="Request timed out", attempt=1, task=short_task)
        g = _graph(n)
        r = Replanner(g)
        action = r.handle_failure(n)
        assert action == RecoveryAction.RETRY


class TestReplannerHistory:
    def test_repair_history_recorded(self):
        n = _node(error="something", attempt=1)
        g = _graph(n)
        history = []
        r = Replanner(g, repair_history=history)
        r.handle_failure(n)
        assert len(history) == 1
        assert history[0].node_id == n.id
