"""
AJA CLI Command: run
====================
Primary mission entry point.
"""

import asyncio
import os
import subprocess
import sys
from aja.interface.modern import console, mission_spinner, print_error, print_info

PYTHON = sys.executable


def cmd_run(objective: str, background: bool = False, dry_run: bool = False):
    """
    Primary mission entry point.
    """
    if not objective:
        print_error("No mission objective provided.")
        raise SystemExit(2)

    if background:
        print_info(f"Dispatching mission to background: {objective}")
        cmd_args = [PYTHON, "-m", "aja", "run", objective]
        if dry_run:
            cmd_args.append("--dry-run")
        try:
            subprocess.Popen(
                cmd_args,
                start_new_session=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except OSError as e:
            print_error(f"Failed to spawn background mission: {e}")
            raise SystemExit(1) from e
        return

    with mission_spinner(objective):
        from aja.orchestration.swarm import SwarmEngine

        engine = SwarmEngine(dry_run=dry_run)
        try:
            asyncio.run(engine.plan_and_execute_batons(objective))
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠ Mission interrupted by user.[/]")
            raise SystemExit(130)
        except Exception as e:
            print_error(f"Swarm Execution Error: {e}")
            # Propagate failure so scripts and CI can detect it.
            raise SystemExit(1) from e
