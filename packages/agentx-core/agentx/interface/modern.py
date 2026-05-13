"""
agentx/interface/modern.py
==========================
Modern UI components for AgentX CLI using 'rich'.
"""

import os
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner
from rich.theme import Theme
from rich.markdown import Markdown
from rich.columns import Columns

# Custom theme for AJA/AgentX
AJA_THEME = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "bold green",
        "mission": "bold magenta",
        "baton": "bold blue",
        "status": "bold white on blue",
    }
)

console = Console(theme=AJA_THEME)

AJA_BANNER = """
 █████╗      ██╗ █████╗ 
██╔══██╗     ██║██╔══██╗
███████║     ██║███████║
██╔══██║██   ██║██╔══██║
██║  ██║╚█████╔╝██║  ██║
╚═╝  ╚═╝ ╚════╝ ╚═╝  ╚═╝
[bold cyan]Assistant to the Joint Agents[/]
"""


def print_banner():
    console.print(
        Panel(
            Text(AJA_BANNER, justify="center", style="bold cyan"), border_style="cyan"
        )
    )


def print_status(mode: str, batons: list, tasks: list):
    print_banner()

    # Header Info
    status_text = Text.assemble(
        ("SYSTEM STATUS: ", "bold"),
        (mode.upper(), "success" if mode.lower() != "offline" else "warning"),
    )
    console.print(status_text)
    console.print("-" * 40)

    # Batons Table
    baton_table = Table(title="Active Mission Batons", expand=True, border_style="blue")
    baton_table.add_column("Baton ID", style="baton")
    baton_table.add_column("Objective", style="italic")
    baton_table.add_column("Last Seen", justify="right")

    if not batons:
        baton_table.add_row("None", "No active missions in progress.", "-")
    for b in batons:
        baton_table.add_row(b["id"], b["objective"][:50], b.get("updated_at", "N/A"))

    console.print(baton_table)

    # Tasks Table
    task_table = Table(title="Recent Task Queue", expand=True, border_style="magenta")
    task_table.add_column("ID", justify="center", style="dim")
    task_table.add_column("Status", justify="center")
    task_table.add_column("Input Fragment")
    task_table.add_column("Updated", justify="right")

    if not tasks:
        task_table.add_row("-", "EMPTY", "Queue is currently clear.", "-")
    for t in tasks:
        status_style = (
            "green"
            if t["status"] == "COMPLETED"
            else "yellow"
            if t["status"] == "PENDING"
            else "red"
        )
        task_table.add_row(
            str(t["id"]),
            Text(t["status"], style=status_style),
            t["input"][:60] + "...",
            t.get("updated_at", "-"),
        )

    console.print(task_table)


def print_doctor(checks: list):
    console.print("\n[bold]AgentX Diagnostics[/]")
    table = Table(show_header=False, box=None)
    for name, status, detail in checks:
        icon = "[bold green]OK[/]" if status else "[bold red]!![/]"
        table.add_row(icon, f"{name}:", detail)
    console.print(table)


def mission_spinner(objective: str):
    return Live(
        Panel(
            Columns(
                [
                    Spinner(
                        "dots", text=Text("Initializing Mission...", style="mission")
                    ),
                    Text(f" Objective: '{objective}'", style="italic cyan"),
                ]
            ),
            title="[bold cyan]AgentX Swarm Engine[/]",
            border_style="cyan",
        ),
        refresh_per_second=10,
        transient=True,
    )


def print_error(msg: str):
    console.print(Panel(Text(msg, style="error"), title="Error", border_style="red"))


def print_success(msg: str):
    console.print(Text(f"✔ {msg}", style="success"))


def print_info(msg: str):
    console.print(Text(f"ℹ {msg}", style="info"))
