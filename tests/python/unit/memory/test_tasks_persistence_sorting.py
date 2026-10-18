"""Unit tests for task persistence query sorting (Task 3.3 / M3).

Verifies that fetch_recent_tasks and fetch_pending_tasks sort the entire table
before slicing to `limit`, ensuring newer tasks and prioritized statuses
are never missed due to premature query limits.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from aja.persistence.tasks import fetch_pending_tasks, fetch_recent_tasks


def test_fetch_recent_tasks_sorts_entire_table_before_limit():
    fake_tasks = []
    # Generate 50 tasks with old timestamps
    for i in range(50):
        fake_tasks.append(
            {
                "task_id": f"T-OLD-{i}",
                "status": "COMPLETED",
                "created_at": f"2026-01-01T00:{i:02d}:00Z",
                "updated_at": f"2026-01-01T00:{i:02d}:00Z",
            }
        )

    # Add 3 brand-new tasks at index 45, 46, 47 (far beyond limit*3 = 9)
    fake_tasks[45] = {
        "task_id": "T-NEW-1",
        "status": "COMPLETED",
        "created_at": "2026-10-05T12:00:00Z",
        "updated_at": "2026-10-05T12:00:00Z",
    }
    fake_tasks[46] = {
        "task_id": "T-NEW-2",
        "status": "COMPLETED",
        "created_at": "2026-10-05T13:00:00Z",
        "updated_at": "2026-10-05T13:00:00Z",
    }
    fake_tasks[47] = {
        "task_id": "T-NEW-3",
        "status": "COMPLETED",
        "created_at": "2026-10-05T14:00:00Z",
        "updated_at": "2026-10-05T14:00:00Z",
    }

    mock_table = MagicMock()
    mock_search = MagicMock()
    mock_table.search.return_value = mock_search
    mock_search.to_list.return_value = list(fake_tasks)

    with patch("aja.persistence.tasks._manager.get_table", return_value=mock_table):
        recent = fetch_recent_tasks(limit=3)

    assert len(recent) == 3
    # The 3 newest tasks must be returned in descending order
    assert recent[0]["task_id"] == "T-NEW-3"
    assert recent[1]["task_id"] == "T-NEW-2"
    assert recent[2]["task_id"] == "T-NEW-1"


def test_fetch_pending_tasks_prioritizes_before_limit():
    # 20 FAILED tasks followed by 1 INTERRUPTED task
    fake_tasks = [
        {"task_id": f"T-FAIL-{i}", "status": "FAILED", "updated_at": "2026-05-01T00:00:00Z"}
        for i in range(20)
    ]
    fake_tasks.append(
        {"task_id": "T-INTERRUPTED", "status": "INTERRUPTED", "updated_at": "2026-05-02T00:00:00Z"}
    )

    mock_table = MagicMock()
    mock_search = MagicMock()
    mock_table.search.return_value = mock_search
    mock_search.where.return_value = mock_search
    mock_search.to_list.return_value = list(fake_tasks)

    with patch("aja.persistence.tasks._manager.get_table", return_value=mock_table):
        pending = fetch_pending_tasks(limit=1)

    assert len(pending) == 1
    # INTERRUPTED has priority 1, must be returned even though it was at index 20
    assert pending[0]["task_id"] == "T-INTERRUPTED"
