"""
AJA CLI Command: tui
====================
Launch interactive terminal TUI dashboard or live kanban view.
"""

import asyncio
import subprocess
import sys

PYTHON = sys.executable


def cmd_tui(dry_run: bool = False):
    """Run the live terminal curses TUI dashboard."""
    from aja.tui.curses_tui import run_curses_tui_main

    asyncio.run(run_curses_tui_main(dry_run=dry_run))


def cmd_live():
    """Run the live kanban view."""
    from aja.tui.kanban import live_kanban

    live_kanban()


def cmd_ui():
    """Launch textual interface TUI."""
    subprocess.run([PYTHON, "-m", "aja.interface.tui"])
