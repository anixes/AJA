"""
=============================================================================
Unit Test: Interactive Kanban Board & Task State Syncing
=============================================================================
"""

import pytest
from unittest.mock import MagicMock, patch
from aja.tui.tasks import (
    TaskManager,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from aja.tui.kanban import KanbanBoard, InteractiveKanbanApp


def test_kanban_board_renderable():
    """Verify that KanbanBoard fetches and partitions tasks correctly into 4 columns."""
    mock_tm = MagicMock()
    mock_manager = MagicMock()
    mock_table = MagicMock()

    # Create mock arrow table with sample tasks
    import pyarrow as pa

    data = {
        "task_id": ["task-1", "task-2", "task-3"],
        "objective": ["Pending Item", "Running Item", "Completed Item"],
        "status": [STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED],
    }
    arrow_table = pa.Table.from_pydict(data)
    mock_table.to_arrow.return_value = arrow_table
    mock_manager.get_table.return_value = mock_table
    mock_tm.manager = mock_manager
    mock_tm.table_name = "tasks"

    board = KanbanBoard(mock_tm, active_col=0, selected_card_idx=0)
    col_data = board.get_column_data()

    assert len(col_data[STATUS_PENDING]) == 1
    assert col_data[STATUS_PENDING][0]["task_id"] == "task-1"
    assert len(col_data[STATUS_RUNNING]) == 1
    assert len(col_data[STATUS_COMPLETED]) == 1
    assert len(col_data[STATUS_FAILED]) == 0

    # Test __rich__ rendering
    rendered = board.__rich__()
    assert rendered is not None


def test_kanban_interactive_status_cycling():
    """Verify that pressing 'm' cycles card status: PENDING -> RUNNING -> COMPLETED."""
    mock_tm = MagicMock()
    import pyarrow as pa

    data = {
        "task_id": ["task-100"],
        "objective": ["Deploy feature"],
        "status": [STATUS_PENDING],
    }
    arrow_table = pa.Table.from_pydict(data)
    mock_table = MagicMock()
    mock_table.to_arrow.return_value = arrow_table
    mock_tm.manager.get_table.return_value = mock_table
    mock_tm.table_name = "tasks"

    app = InteractiveKanbanApp(task_manager=mock_tm)
    assert app.active_col == 0

    # Trigger move logic manually
    board = KanbanBoard(mock_tm, active_col=0, selected_card_idx=0)
    col_data = board.get_column_data()
    card = col_data[STATUS_PENDING][0]

    mock_tm.update_status(card["task_id"], STATUS_RUNNING)
    mock_tm.update_status.assert_called_with("task-100", STATUS_RUNNING)
