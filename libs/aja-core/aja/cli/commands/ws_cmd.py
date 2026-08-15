"""
AJA CLI Command: ws
====================
Multi-Workspace Management commands for the AJA Agent OS Kernel.
Commands:
  aja ws list
  aja ws add <path> [--name <name>]
  aja ws use <name_or_id>
  aja ws remove <name_or_id>
  aja ws run <name_or_id> "<objective>"
  aja ws status
"""

import asyncio
from pathlib import Path
from typing import List

from rich.table import Table

from aja.interface.modern import console, print_error, print_info, print_success
from aja.workspace.manager import get_workspace_registry


def cmd_ws(args: List[str]):
    """Manage multiple project workspaces in the AJA Agent OS."""
    if not args:
        _show_ws_help()
        return

    subcmd = args[0].lower()
    reg = get_workspace_registry()

    if subcmd in ("list", "ls"):
        workspaces = reg.list_all()
        if not workspaces:
            print_info("No workspaces registered yet. Run 'aja ws add <path>' to register one.")
            return

        table = Table(title="🗂️  AJA Agent OS — Registered Workspaces", border_style="cyan")
        table.add_column("Status", style="bold", justify="center")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold green")
        table.add_column("Path", style="cyan")
        table.add_column("Created", style="dim")

        for ws in workspaces:
            status = "● ACTIVE" if ws.active else "○"
            table.add_row(
                f"[green]{status}[/green]" if ws.active else f"[dim]{status}[/dim]",
                ws.id,
                ws.name,
                ws.path,
                ws.created_at,
            )
        console.print(table)

    elif subcmd == "add":
        if len(args) < 2:
            print_error("Usage: aja ws add <path> [--name <name>]")
            return

        target_path = args[1]
        name = None
        if "--name" in args:
            idx = args.index("--name")
            if idx + 1 < len(args):
                name = args[idx + 1]

        try:
            ws = reg.add(path=target_path, name=name, set_active=True)
            print_success(f"Registered and activated workspace '[bold]{ws.name}[/bold]' ({ws.id}) at {ws.path}")
        except Exception as e:
            print_error(f"Failed to add workspace: {e}")

    elif subcmd in ("use", "switch", "activate"):
        if len(args) < 2:
            print_error("Usage: aja ws use <name_or_id>")
            return

        target = args[1]
        if reg.set_active(target):
            ws = reg.get(target)
            print_success(f"Switched active workspace to '[bold]{ws.name}[/bold]' ({ws.path})")
        else:
            print_error(f"Workspace '{target}' not found. Run 'aja ws list' to view available workspaces.")

    elif subcmd in ("remove", "rm", "delete"):
        if len(args) < 2:
            print_error("Usage: aja ws remove <name_or_id>")
            return

        target = args[1]
        if reg.remove(target):
            print_success(f"Removed workspace '{target}' from registry.")
        else:
            print_error(f"Workspace '{target}' not found.")

    elif subcmd == "status":
        from aja.kernel.scheduler import get_kernel_scheduler

        active_ws = reg.get_active()
        scheduler = get_kernel_scheduler()
        active_missions = scheduler.list_active()

        console.print("\n[bold cyan]🖥️  AJA Agent OS Kernel Status[/bold cyan]")
        if active_ws:
            console.print(f"  [bold]Active Workspace:[/] [bold green]{active_ws.name}[/] ({active_ws.path})")
        else:
            console.print("  [bold]Active Workspace:[/] [dim]None (will use default)[/dim]")

        console.print(f"  [bold]Total Workspaces Registered:[/] {len(reg.list_all())}")
        console.print(f"  [bold]Active Swarm Missions in Queue:[/] {len(active_missions)}")

        if active_missions:
            table = Table(title="Active Swarms", border_style="yellow")
            table.add_column("Mission ID", style="bold")
            table.add_column("Workspace", style="green")
            table.add_column("Status", style="cyan")
            table.add_column("Objective", style="dim")
            for m in active_missions:
                table.add_row(m.id, m.workspace_name, m.status.value, m.objective[:40] + "...")
            console.print(table)
        console.print("")

    elif subcmd == "run":
        if len(args) < 3:
            print_error('Usage: aja ws run <name_or_id> "<objective>"')
            return

        target_ws = args[1]
        objective = " ".join(args[2:])

        ws = reg.get(target_ws)
        if not ws:
            print_error(f"Workspace '{target_ws}' not found.")
            return

        console.print(f"\n[bold cyan]🚀 Dispatching Swarm Mission to Workspace:[/] [bold green]{ws.name}[/bold green]")
        console.print(f"   [bold]Path:[/] {ws.path}")
        console.print(f"   [bold]Objective:[/] {objective}\n")

        from aja.kernel.scheduler import get_kernel_scheduler, PriorityLevel
        scheduler = get_kernel_scheduler()

        async def _run():
            await scheduler.start()
            req = await scheduler.submit(
                objective=objective,
                workspace_id=ws.id,
                priority=PriorityLevel.NORMAL,
                source="cli",
            )
            while req.status in ("queued", "running"):
                await asyncio.sleep(0.5)
            await scheduler.stop()
            return req

        try:
            req = asyncio.run(_run())
            if req.status == "completed":
                print_success(f"Mission {req.id} completed successfully on workspace '{ws.name}'.")
            else:
                print_error(f"Mission {req.id} ended with status: {req.status}. Error: {req.error}")
        except Exception as e:
            print_error(f"Execution failed: {e}")

    else:
        _show_ws_help()


def _show_ws_help():
    console.print("""
[bold cyan]AJA Agent OS — Multi-Workspace Subcommands (`aja ws`)[/bold cyan]

[bold yellow]Commands:[/]
  [bold green]aja ws list[/]                      List all registered project workspaces
  [bold green]aja ws add <path> [--name <name>][/] Register a new workspace into the Kernel
  [bold green]aja ws use <name_or_id>[/]           Switch default active workspace
  [bold green]aja ws remove <name_or_id>[/]        Unregister a workspace
  [bold green]aja ws run <name> "<goal>"[/]        Dispatch an autonomous mission to a workspace
  [bold green]aja ws status[/]                    Show active workspace & running swarm missions
""")
