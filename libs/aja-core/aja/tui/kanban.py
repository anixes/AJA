"""
aja/tui/kanban.py
=================
Interactive Mission Kanban Board for AJA Swarm.
Supports keyboard navigation, card status transitions, inline creation,
and real-time zero-copy synchronization with PyArrow / LanceDB journals.
"""

import time
from typing import List, Dict, Any, Optional
import pyarrow as pa
import pyarrow.compute as pc

from rich.console import Console, RenderableType
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.live import Live
from rich.box import ROUNDED, DOUBLE, HEAVY
from rich.prompt import Prompt

from .tasks import (
    TaskManager,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_FAILED,
    STATUS_COMPLETED,
)
from .terminal import read_key, run_fullscreen_modal, is_interactive_tty

console = Console()

STATUSES = [STATUS_PENDING, STATUS_RUNNING, STATUS_FAILED, STATUS_COMPLETED]
STATUS_COLORS = {
    STATUS_PENDING: "cyan",
    STATUS_RUNNING: "yellow",
    STATUS_FAILED: "red",
    STATUS_COMPLETED: "green",
}


class KanbanBoard:
    """
    Renders a 4-column Transactional Kanban board with selection highlights.
    """

    def __init__(
        self,
        task_manager: TaskManager,
        active_col: int = 0,
        selected_card_idx: int = 0,
        modal_message: Optional[str] = None,
    ):
        self.task_manager = task_manager
        self.active_col = active_col
        self.selected_card_idx = selected_card_idx
        self.modal_message = modal_message

    def get_column_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch and partition tasks by status using fast Arrow compute."""
        cols = {s: [] for s in STATUSES}
        try:
            full_table = self.task_manager.manager.get_table(
                self.task_manager.table_name
            ).to_arrow()
            if len(full_table) > 0 and "status" in full_table.schema.names:
                for s in STATUSES:
                    filtered = full_table.filter(pc.equal(full_table["status"], s))
                    if len(filtered) > 0:
                        ids = filtered["task_id"].to_pylist()
                        objs = filtered["objective"].to_pylist()
                        cols[s] = [
                            {"task_id": tid, "objective": obj}
                            for tid, obj in zip(ids, objs)
                        ]
        except Exception:
            pass
        return cols

    def __rich__(self) -> RenderableType:
        col_data = self.get_column_data()

        # Build columns
        column_panels = []
        for i, status in enumerate(STATUSES):
            is_col_focused = i == self.active_col
            color = STATUS_COLORS[status]
            cards = col_data[status]
            card_renderables = []

            if not cards:
                card_renderables.append(
                    Text("No tasks in queue", style="italic grey50")
                )
            else:
                for c_idx, task in enumerate(cards):
                    is_card_selected = is_col_focused and c_idx == self.selected_card_idx
                    border = "bold bright_white" if is_card_selected else color
                    bg = "on #1c2128" if is_card_selected else ""
                    sel_indicator = "▶ " if is_card_selected else "  "

                    card_text = (
                        f"{sel_indicator}[bold white]{task['objective']}[/]\n"
                        f"   [dim]ID: {task['task_id']}[/dim]"
                    )
                    card_renderables.append(
                        Panel(
                            card_text,
                            border_style=border,
                            style=bg,
                            padding=(0, 1),
                            box=HEAVY if is_card_selected else ROUNDED,
                        )
                    )

            col_title = f"{status.upper()} ({len(cards)})"
            if is_col_focused:
                col_title = f"[bold bright_white]● {col_title} ●[/]"
            else:
                col_title = f"[dim]{col_title}[/dim]"

            col_panel = Panel(
                Columns(card_renderables, align="center") if card_renderables else Text("Empty"),
                title=col_title,
                border_style="bright_white" if is_col_focused else color,
                box=DOUBLE if is_col_focused else ROUNDED,
                width=36,
            )
            column_panels.append(col_panel)

        grid = Table.grid(expand=True, padding=1)
        for _ in STATUSES:
            grid.add_column(justify="center")
        grid.add_row(*column_panels)

        # Footer controls
        controls = (
            "[bold cyan][Tab/←/→][/] Switch Col  |  "
            "[bold cyan][↑/↓][/] Select Card  |  "
            "[bold yellow][m][/] Move Status  |  "
            "[bold green][a][/] Add Task  |  "
            "[bold red][d][/] Delete  |  "
            "[bold white][q/Esc][/] Exit"
        )
        footer_panel = Panel(
            Text.from_markup(controls, justify="center"),
            border_style="dim white",
            box=ROUNDED,
        )

        main_table = Table.grid(expand=True)
        main_table.add_column()
        main_table.add_row(grid)
        if self.modal_message:
            main_table.add_row(
                Panel(
                    f"[bold yellow]{self.modal_message}[/bold yellow]",
                    title="Notification",
                    border_style="yellow",
                    box=ROUNDED,
                )
            )
        main_table.add_row(footer_panel)

        return Panel(
            main_table,
            title="[bold bright_cyan]═══ AJA MISSION KANBAN CONTROL ═══[/]",
            border_style="bright_blue",
            box=ROUNDED,
        )


class InteractiveKanbanApp:
    """Interactive loop for navigating and manipulating the Kanban board."""

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self.task_manager = task_manager or TaskManager()
        self.active_col = 0
        self.selected_card_idx = 0
        self.running = True
        self.notification = None

    def run(self):
        if not is_interactive_tty():
            # Non-interactive fallback: render single static view
            board = KanbanBoard(self.task_manager)
            console.print(board)
            return

        with Live(
            KanbanBoard(self.task_manager, self.active_col, self.selected_card_idx),
            refresh_per_second=10,
            screen=True,
            transient=True,
        ) as live:
            while self.running:
                board = KanbanBoard(
                    self.task_manager,
                    self.active_col,
                    self.selected_card_idx,
                    self.notification,
                )
                live.update(board)

                key = read_key(timeout=0.08)
                if not key:
                    continue

                self.notification = None
                col_data = board.get_column_data()
                curr_status = STATUSES[self.active_col]
                curr_cards = col_data[curr_status]

                if key in ("q", "escape"):
                    self.running = False
                    break
                elif key in ("tab", "right"):
                    self.active_col = (self.active_col + 1) % len(STATUSES)
                    self.selected_card_idx = 0
                elif key == "left":
                    self.active_col = (self.active_col - 1) % len(STATUSES)
                    self.selected_card_idx = 0
                elif key == "up":
                    if self.selected_card_idx > 0:
                        self.selected_card_idx -= 1
                elif key == "down":
                    if self.selected_card_idx < len(curr_cards) - 1:
                        self.selected_card_idx += 1
                elif key == "m":
                    # Cycle status: PENDING -> RUNNING -> COMPLETED
                    if curr_cards and self.selected_card_idx < len(curr_cards):
                        card = curr_cards[self.selected_card_idx]
                        tid = card["task_id"]
                        next_status = (
                            STATUS_RUNNING
                            if curr_status == STATUS_PENDING
                            else STATUS_COMPLETED
                            if curr_status == STATUS_RUNNING
                            else STATUS_PENDING
                        )
                        self.task_manager.update_status(tid, next_status)
                        self.notification = f"Moved task {tid} to {next_status.upper()}"
                        self.selected_card_idx = max(0, self.selected_card_idx - 1)
                elif key == "d":
                    if curr_cards and self.selected_card_idx < len(curr_cards):
                        card = curr_cards[self.selected_card_idx]
                        tid = card["task_id"]
                        self.task_manager.delete_task(tid)
                        self.notification = f"Deleted task {tid}"
                        self.selected_card_idx = max(0, self.selected_card_idx - 1)
                elif key == "a":
                    # Quick task creation
                    live.stop()
                    console.print("\n[bold cyan]─── Add New Mission Task ───[/]")
                    new_obj = Prompt.ask("Enter task objective (or Enter to cancel)")
                    if new_obj:
                        tid = self.task_manager.add_task(new_obj)
                        self.notification = f"Added task {tid}: {new_obj}"
                    live.start()


def live_kanban():
    """Starts the full-screen interactive Kanban dashboard."""
    app = InteractiveKanbanApp()
    run_fullscreen_modal(app.run)


def render_kanban_board(task_manager: TaskManager):
    """Render a static snapshot of the Kanban board."""
    board = KanbanBoard(task_manager)
    console.print(board)


def show_kanban():
    """Interactive launch entry point."""
    live_kanban()
