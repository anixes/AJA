from rich.console import Console, RenderableType
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.live import Live
import pyarrow as pa
import pyarrow.compute as pc
import time
from .tasks import (
    TaskManager, 
    STATUS_PENDING, 
    STATUS_RUNNING, 
    STATUS_FAILED, 
    STATUS_COMPLETED
)

console = Console()

class KanbanBoard:
    """
    Renders a 4-column Transactional Kanban board optimized for performance.
    Designed for use with rich.live.
    """
    def __init__(self, task_manager: TaskManager):
        self.task_manager = task_manager

    def __rich__(self) -> RenderableType:
        # Fetch full table once (PERF-03)
        try:
            full_table = self.task_manager.manager.get_table(self.task_manager.table_name).to_arrow()
        except:
            return Panel("Database Locked or Uninitialized", title="[bold red]ERROR[/]", border_style="red")

        status_col = full_table["status"] if len(full_table) > 0 else None

        def get_by_status(status_val):
            if status_col is None:
                return pa.Table.from_batches([], schema=full_table.schema)
            return full_table.filter(pc.equal(status_col, status_val))

        pending_table = get_by_status(STATUS_PENDING)
        running_table = get_by_status(STATUS_RUNNING)
        failed_table = get_by_status(STATUS_FAILED)
        completed_table = get_by_status(STATUS_COMPLETED)

        def make_column(title: str, arrow_table: pa.Table, color: str):
            content = []
            if len(arrow_table) == 0:
                content.append(Text("No missions", style="italic grey50"))
            else:
                ids = arrow_table["task_id"].to_pylist()
                objectives = arrow_table["objective"].to_pylist()
                
                for tid, obj in zip(ids, objectives):
                    content.append(Panel(
                        f"[white]{obj}[/white]\n[grey50]ID: {tid}[/grey50]",
                        border_style=color,
                        padding=(0, 1),
                        expand=True
                    ))
            
            return Panel(
                Columns(content, align="center"),
                title=f"[bold {color}]{title}[/bold {color}]",
                border_style=color,
                width=35
            )

        grid = Table.grid(expand=True, padding=1)
        grid.add_column("Pending", justify="center")
        grid.add_column("Running", justify="center")
        grid.add_column("Failed", justify="center")
        grid.add_column("Completed", justify="center")

        grid.add_row(
            make_column("PENDING", pending_table, "cyan"),
            make_column("RUNNING", running_table, "yellow"),
            make_column("FAILED", failed_table, "red"),
            make_column("COMPLETED", completed_table, "green")
        )

        return Panel(grid, title="[bold white]AJA MISSION CONTROL[/bold white]", border_style="bright_blue")

def live_kanban():
    """Starts a real-time Kanban telemetry loop."""
    tm = TaskManager()
    board = KanbanBoard(tm)
    with Live(board, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

def render_kanban_board(task_manager: TaskManager):
    """Legacy compatibility function."""
    board = KanbanBoard(task_manager)
    console.print(board)

def show_kanban():
    tm = TaskManager()
    render_kanban_board(tm)

