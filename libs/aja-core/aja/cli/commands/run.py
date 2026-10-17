"""
AJA CLI Command: run
====================
Primary mission entry point.
"""

import asyncio
import os
import subprocess
import sys
from typing import Optional
from aja.interface.modern import console, mission_spinner, print_error, print_info

PYTHON = sys.executable


def cmd_run(
    objective: str,
    background: bool = False,
    dry_run: bool = False,
    swarm: bool = False,
    no_plan: bool = False,
):
    """
    Primary mission entry point.
    By default runs the 2026 Autonomous Tool Harness (direct_loop with Verification Ladder
    and Living Disk State). Pass swarm=True for legacy multi-agent baton passing.
    """
    if not objective:
        print_error("No mission objective provided.")
        raise SystemExit(2)

    if background:
        print_info(f"Dispatching mission to background: {objective}")
        cmd_args = [PYTHON, "-m", "aja", "run", objective]
        if dry_run:
            cmd_args.append("--dry-run")
        if swarm:
            cmd_args.append("--swarm")
        if no_plan:
            cmd_args.append("--no-plan")
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

    # Fallback to legacy SwarmEngine if explicitly requested
    if swarm:
        with mission_spinner(objective):
            import logging

            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
            from aja.orchestration.swarm import SwarmEngine

            engine = SwarmEngine(dry_run=dry_run)
            try:
                asyncio.run(engine.plan_and_execute_batons(objective))
                return
            except KeyboardInterrupt:
                console.print("\n[yellow]⚠ Mission interrupted by user.[/]")
                raise SystemExit(130)
            except Exception as e:
                print_error(f"Swarm Execution Error: {e}")
                raise SystemExit(1) from e

    # Modern Autonomous Harness (direct_loop with Verification Ladder & Living Disk State)
    async def _run_harness():
        from pathlib import Path
        from aja.llm import get_gateway, resolve_active_model_string
        from aja.orchestration.direct_loop import run_direct_loop
        from aja.orchestration.plan_state import init_plan
        from aja.orchestration.plan_gate import plan_gate
        from aja.tools.registry import ToolsRegistry
        from aja.tools.executor import ToolExecutor

        model = resolve_active_model_string()
        gateway = get_gateway(model)
        tools_reg = ToolsRegistry()
        executor = ToolExecutor(registry=tools_reg)

        plan_file: Optional[Path] = None
        if not no_plan:
            if Path("PLAN.md").exists():
                plan_file = Path("PLAN.md")
            else:
                try:
                    plan_result = await plan_gate(objective)
                    if "Execution Plan to follow:" in plan_result:
                        plan_file = Path("PLAN.md")
                        init_plan(
                            objective=objective,
                            tasks=["Analyze requirements", "Execute changes", "Verify solution"],
                            path=plan_file,
                            overwrite=True,
                        )
                except Exception as e:
                    console.print(f"[dim]Plan gate skipped: {e}[/dim]")

        outcome = await run_direct_loop(
            objective=objective,
            gateway=gateway,
            tools_registry=tools_reg,
            executor=executor,
            model=model,
            dry_run=dry_run,
            ladder_verify=True,
            auto_verify=True,
            plan_path=plan_file,
            use_plan=bool(plan_file),
            console=console,
        )

        if outcome and outcome.get("status") == "completed":
            console.print("[bold green]✔ Mission completed and verified.[/bold green]")
        else:
            reason = outcome.get("reason", "unknown") if outcome else "null_outcome"
            print_error(f"Mission incomplete: {reason}")
            raise SystemExit(1)

    try:
        asyncio.run(_run_harness())
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Mission interrupted by user.[/]")
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception as e:
        print_error(f"Harness Execution Error: {e}")
        raise SystemExit(1) from e
