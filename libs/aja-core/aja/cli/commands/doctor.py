"""
AJA CLI Command: doctor
=======================
System health checks and diagnostics.
"""

import json
import sys
from aja.interface.modern import print_doctor


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

    if ci_mode:
        from aja.interface.modern import console

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
