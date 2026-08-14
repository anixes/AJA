"""
AJA CLI Command: daemon
=======================
Supervise and manage background Gateway and Worker daemons.
"""

from typing import List
from aja.interface.modern import console, print_error, print_info, print_success


def cmd_daemon(args: List[str]):
    """Manage background daemon services (start, status, stop)."""
    from aja.gateway.daemon_manager import DaemonManager

    subcmd = args[0].lower() if args else "status"
    mgr = DaemonManager()

    if subcmd == "start":
        res = mgr.start_daemon()
        if res["status"] == "already_running":
            print_info(f"AJA daemon is already running (PID: {res['pid']}).")
        else:
            print_success(
                f"Successfully started AJA Gateway & Worker daemon supervisor (PID: {res['pid']})."
            )

    elif subcmd == "status":
        status = mgr.get_status()
        if status["status"] == "running":
            console.print(
                f"[bold green]✔ AJA Daemon is ACTIVE[/bold green] (PID: {status['pid']})"
            )
            console.print(f"  [bold]Started At:[/] {status.get('started_at', '-')}")
            console.print(
                f"  [bold]Services:[/] {', '.join(status.get('services', []))}"
            )
        else:
            console.print("[bold yellow]ℹ AJA Daemon is STOPPED[/bold yellow]")

    elif subcmd == "stop":
        res = mgr.stop_daemon()
        if res["status"] == "not_running":
            print_info("AJA daemon is not currently running.")
        else:
            print_success("Successfully stopped AJA daemon processes.")

    else:
        print_error("Usage: aja daemon <start|status|stop>")
