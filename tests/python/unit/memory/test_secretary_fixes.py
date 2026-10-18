"""Regression tests for secretary bug fixes:

- BUG 1: publish_heartbeat must not NULL-out worker registry profile fields.
- BUG 2: reject_communication must succeed (rejection_reason is in schema).
- BUG 3: cleanup_old_approvals must honor nested metadata_json.approval_expires_at
  with normalized (Z-suffix safe) timestamp comparison.
- BUG 4: log_worker_execution uses drift-free integer counters.
"""

import json

import pytest

from aja.memory.secretary import AJAMemory


@pytest.fixture()
def memory(tmp_path):
    return AJAMemory(db_path=str(tmp_path / "lancedb"))


def test_heartbeat_preserves_worker_profile(memory):
    """Heartbeats must only touch hostname/pid/last_heartbeat/status/name;
    registry fields survive three consecutive heartbeat publishes."""
    memory.create_worker(
        {
            "worker_id": "w-profile",
            "worker_name": "Profile Worker",
            "worker_type": "local",
            "description": "profile preservation probe",
            "model": "gpt-4o",
            "reliability_score": 0.87,
            "execution_speed": "fast",
            "cost_profile": "free",
            "primary_strengths": ["shell", "web"],
            "preferred_task_types": ["code", "research"],
        }
    )

    for _ in range(3):
        memory.publish_heartbeat("w-profile", status="ONLINE", name="autonomous-loop")

    w = memory.get_worker("w-profile")
    assert w is not None
    assert w["status"] == "ONLINE"
    assert w["last_heartbeat"]
    assert w["name"] == "autonomous-loop"
    # Registry profile intact after 3 heartbeats.
    assert w["worker_name"] == "Profile Worker"
    assert w["model"] == "gpt-4o"
    assert w["description"] == "profile preservation probe"
    assert w["reliability_score"] == pytest.approx(0.87)
    assert w["primary_strengths"] == ["shell", "web"]
    assert w["preferred_task_types"] == ["code", "research"]

    # Heartbeat on an unknown worker creates a full, well-formed row.
    memory.publish_heartbeat("w-fresh", status="ONLINE", name="loop")
    fresh = memory.get_worker("w-fresh")
    assert fresh is not None
    assert fresh["reliability_score"] == pytest.approx(0.5)
    assert fresh["success_count"] == 0
    assert fresh["fail_count"] == 0


def test_reject_communication_succeeds(memory):
    """reject_communication writes rejection_reason without raising."""
    comm = memory.create_communication({"recipient": "alice", "content": "hello"})
    mid = comm["message_id"]

    updated = memory.reject_communication(mid, reason="wrong recipient")

    assert updated is not None
    assert updated["approval_status"] == "rejected"
    assert updated["rejection_reason"] == "wrong recipient"
    # Original body untouched by rejection.
    assert updated["content"] == "hello"


def test_mark_communication_sent_keeps_content(memory):
    """Delivery note must not overwrite the original message body."""
    comm = memory.create_communication({"recipient": "bob", "content": "the body"})
    mid = comm["message_id"]

    updated = memory.mark_communication_sent(mid, note="delivered via telegram")

    assert updated["delivery_status"] == "sent"
    assert "the body" in updated["content"]
    assert "delivered via telegram" in updated["content"]


def test_cleanup_old_approvals_nested_expiry(memory):
    """Expired approvals are removed via metadata_json.approval_expires_at,
    including Z-suffixed ISO timestamps; fresh approvals survive."""
    expired_id = memory.create_approval(
        {
            "kind": "communication",
            "description": "stale approval",
            "metadata": {"approval_expires_at": "2020-01-01T00:00:00Z"},
        }
    )
    fresh_id = memory.create_approval(
        {
            "kind": "communication",
            "description": "still valid",
            "metadata": {"approval_expires_at": "2999-01-01T00:00:00Z"},
        }
    )

    removed = memory.cleanup_old_approvals(ttl_days=30)

    assert removed == 1
    assert memory.get_approval(expired_id) is None
    assert memory.get_approval(fresh_id) is not None


def test_cleanup_old_approvals_terminal_status_normalized_ts(memory):
    """Terminal-status staleness also uses fromisoformat normalization,
    so Z-suffixed updated_at rows are cleaned instead of string-mismatched."""
    aid = memory.create_approval({"kind": "manual", "description": "old resolved"})
    table = memory.db.open_table("aja_approvals")
    table.update(
        where=f"approval_id = '{aid}'",
        values={"status": "resolved", "updated_at": "2020-06-01T12:00:00Z"},
    )

    assert memory.cleanup_old_approvals(ttl_days=30) == 1
    assert memory.get_approval(aid) is None


def test_log_worker_execution_integer_counters_no_drift(memory):
    """7 successes + 3 failures -> exact integer counters and clean rate."""
    memory.create_worker({"worker_id": "w-stats", "worker_name": "Stats Worker"})

    for i in range(7):
        memory.log_worker_execution(
            {"worker_id": "w-stats", "task_id": f"t{i}", "objective": "ok", "success": True}
        )
    for i in range(3):
        memory.log_worker_execution(
            {
                "worker_id": "w-stats",
                "task_id": f"f{i}",
                "objective": "boom",
                "success": False,
                "error": f"err-{i}",
            }
        )

    w = memory.get_worker("w-stats")
    assert w["success_count"] == 7
    assert w["fail_count"] == 3
    assert w["total_tasks_executed"] == 10
    # Derived display rate is exact, no accumulated float drift.
    assert w["historical_success_rate"] == pytest.approx(70.0)
    assert len(w["recent_failures"]) == 3

    # Counters persist as int32-typed schema columns.
    raw = (
        memory.db.open_table("aja_workers")
        .search()
        .where("worker_id = 'w-stats'")
        .limit(1)
        .to_list()[0]
    )
    assert isinstance(raw["success_count"], int)
    assert isinstance(raw["fail_count"], int)
