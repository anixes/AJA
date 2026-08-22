"""
AJA CLI Command: doctor
=======================
System health checks and diagnostics.
"""

import json
import sys
from rich.table import Table
from rich.box import ROUNDED

from aja.interface.modern import console, print_doctor


def _print_startup_checks(results):
    """Render startup check results grouped by severity (errors last, red)."""
    errors = [r for r in results if r.severity == "error"]
    warnings = [r for r in results if r.severity == "warning"]
    oks = [r for r in results if r.severity == "ok"]

    icons = {"error": "✘ ERR", "warning": "! WARN", "ok": "✔ OK"}
    styles = {"error": "bold red", "warning": "yellow", "ok": "dim"}

    console.print("\n[bold cyan]═══ Startup Configuration Validation ═══[/]")
    table = Table(show_header=False, box=ROUNDED, expand=True, border_style="cyan")
    table.add_column("Status", width=8, justify="center")
    table.add_column("Check", style="bold white", width=24)
    table.add_column("Detail")
    for group in (warnings, oks, errors):
        for r in group:
            table.add_row(
                f"[{styles[r.severity]}]{icons[r.severity]}[/]",
                r.name,
                r.detail,
            )
    console.print(table)


def cmd_doctor(ci_mode: bool = False, agent_mode: bool = False):
    """System health checks and diagnostics."""
    from aja.utils.diagnostics import run_diagnostics

    checks = run_diagnostics()

    if agent_mode:
        output = {
            "status": "ok" if all(status for name, status, msg in checks) else "failed",
            "checks": [
                {"name": name, "passed": bool(status), "message": msg}
                for name, status, msg in checks
            ],
        }
        print(json.dumps(output, indent=2), flush=True)
        if ci_mode:
            critical_checks = {"Native Engine", "Memory Manager", "Config Validation"}
            critical_failures = [
                name
                for name, status, msg in checks
                if not status and name in critical_checks
            ]
            if critical_failures:
                sys.exit(1)
        return

    print_doctor(checks)

    # Startup configuration validation (fast, no network/DB)
    from aja.utils.startup_checks import run_startup_checks

    _print_startup_checks(run_startup_checks())

    if ci_mode:
        critical_checks = {"Native Engine", "Memory Manager", "Config Validation"}
        failures = [name for name, status, msg in checks if not status]
        critical_failures = [f for f in failures if f in critical_checks]

        if critical_failures:
            console.print(
                f"[bold red]CI Mode: Diagnostics failed for: {', '.join(critical_failures)}[/bold red]"
            )
            sys.exit(1)
        elif failures:
            console.print(
                f"[bold yellow]CI Mode: Warnings for: {', '.join(failures)} (non-blocking)[/bold yellow]"
            )
        else:
            console.print("[bold green]CI Mode: All diagnostics passed.[/bold green]")
