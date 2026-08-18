"""
aja/interface/modern.py
==========================
Modern UI components and Design System for AJA CLI using 'rich'.
"""

import os
import sys
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional
from rich.console import Console, RenderableType
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner
from rich.theme import Theme
from rich.markdown import Markdown
from rich.columns import Columns
from rich.box import ROUNDED, HEAVY, DOUBLE, SIMPLE

# Custom theme for AJA (Assistant of Joint Agents)
AJA_THEME = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "bold green",
        "mission": "bold magenta",
        "baton": "bold blue",
        "status": "bold white on blue",
        "agent": "bold bright_cyan",
        "user": "bold bright_white",
        "tool": "bold bright_yellow",
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
Assistant of Joint Agents
"""


def print_banner():
    console.print(
        Panel(
            Text.from_markup(AJA_BANNER, justify="center"),
            border_style="cyan",
            box=ROUNDED,
        )
    )


def render_agent_card(content: str, model: Optional[str] = None, role: str = "AJA") -> Panel:
    """Render a framed, formatted message box for agent responses."""
    md = Markdown(content)
    subtitle = f"[dim cyan]{model}[/]" if model else None
    return Panel(
        md,
        title=f"[bold bright_cyan]🤖 [Agent] {role}[/]",
        subtitle=subtitle,
        border_style="cyan",
        box=ROUNDED,
        padding=(0, 1),
    )


def render_tool_badge(
    tool_name: str,
    success: bool,
    execution_ms: Optional[float] = None,
    data: Optional[str] = None,
    error: Optional[str] = None,
) -> Panel:
    """Render a structured tool execution badge with output summary."""
    status_icon = "[bold green]✔[/]" if success else "[bold red]✘[/]"
    status_text = "[green]SUCCESS[/]" if success else "[red]FAILED[/]"
    ms_tag = f" [dim]({execution_ms:.1f}ms)[/]" if execution_ms is not None else ""

    title = f"{status_icon} [bold yellow]Tool:[/] [bold white]{tool_name}[/] [{status_text}]{ms_tag}"
    content = []

    if data:
        preview = data.strip()
        if len(preview) > 300:
            preview = preview[:300] + "\n[dim]...(output truncated)[/dim]"
        content.append(preview)

    if error:
        content.append(f"[bold red]Error:[/] {error.strip()}")

    body = "\n".join(content) if content else "[dim italic](No output returned)[/]"
    border = "green" if success else "red"

    return Panel(
        body,
        title=title,
        border_style=border,
        box=ROUNDED,
        padding=(0, 1),
    )


def render_help_grid(commands: List[Tuple[str, str]]) -> Panel:
    """Render a structured 2-column command reference table."""
    table = Table(box=ROUNDED, expand=True, border_style="cyan")
    table.add_column("Command", style="bold cyan", width=22)
    table.add_column("Description", style="white")

    for cmd, desc in commands:
        table.add_row(cmd, desc)

    return Panel(table, title="[bold cyan]AJA Command & Modal Hub[/]", border_style="cyan")


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
    baton_table = Table(title="Active Mission Batons", expand=True, border_style="blue", box=ROUNDED)
    baton_table.add_column("Baton ID", style="baton")
    baton_table.add_column("Objective", style="italic")
    baton_table.add_column("Last Seen", justify="right")

    if not batons:
        baton_table.add_row("None", "No active missions in progress.", "-")
    for b in batons:
        baton_table.add_row(b["id"], b["objective"][:50], b.get("updated_at", "N/A"))

    console.print(baton_table)

    # Tasks Table
    task_table = Table(title="Recent Task Queue", expand=True, border_style="magenta", box=ROUNDED)
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
    console.print("\n[bold cyan]═══ AJA Diagnostics & System Readiness ═══[/]")
    table = Table(show_header=False, box=ROUNDED, expand=True, border_style="cyan")
    table.add_column("Status", width=6, justify="center")
    table.add_column("Component", style="bold white", width=24)
    table.add_column("Diagnostics Detail")

    for name, status, detail in checks:
        icon = "[bold green]✔ OK[/]" if status else "[bold red]✘ ERR[/]"
        table.add_row(icon, name, detail)
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
            title="[bold cyan]AJA Swarm Engine[/]",
            border_style="cyan",
            box=ROUNDED,
        ),
        refresh_per_second=10,
        transient=True,
    )


def print_error(msg: str):
    console.print(Panel(Text(msg, style="error"), title="Error", border_style="red", box=ROUNDED))


def print_success(msg: str):
    icon = "✔" if os.name != "nt" else "[OK]"
    console.print(Text(f"{icon} {msg}", style="success"))


def print_info(msg: str):
    icon = "ℹ" if os.name != "nt" else "[INFO]"
    console.print(Text(f"{icon} {msg}", style="info"))
