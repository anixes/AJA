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


FALLBACK_INITIAL_TASKS = ["Analyze requirements", "Execute changes", "Verify solution"]


async def generate_initial_plan_tasks(objective: str, gateway, model_id: str) -> list[str]:
    """
    Generate tailored initial tasks dynamically using the LLM with hard timeout and schema guards.
    Falls back reliably to FALLBACK_INITIAL_TASKS on any failure.
    """
    import json
    import logging
    logger = logging.getLogger(__name__)

    prompt = (
        f"You are an autonomous engineering planning agent.\n"
        f"Objective: \"{objective}\"\n\n"
        "Generate a concise, concrete execution plan.\n"
        "Return ONLY a valid JSON array of 3 to 7 actionable step strings (e.g. [\"Step 1\", \"Step 2\", \"Step 3\"]).\n"
        "Do not include any explanation, conversational text, or markdown formatting."
    )

    try:
        coro = gateway.chat(
            model=model_id,
            prompt=[{"role": "user", "content": prompt}],
            system="You are an autonomous planning engine. Output only a raw JSON array of 3 to 7 string tasks.",
        )
        resp = await asyncio.wait_for(coro, timeout=10.0)
        raw_text = resp if isinstance(resp, str) else (resp.get("content", "") if isinstance(resp, dict) else "")
        clean = raw_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1]
        if clean.endswith("```"):
            clean = clean.rsplit("\n", 1)[0]
        start_idx = clean.find("[")
        end_idx = clean.rfind("]")
        if start_idx != -1 and end_idx != -1:
            clean = clean[start_idx : end_idx + 1]

        data = json.loads(clean)
        if isinstance(data, list) and 3 <= len(data) <= 7 and all(isinstance(t, str) and t.strip() for t in data):
            return [t.strip() for t in data]
        logger.debug("[run.py] Dynamic plan schema invalid, using fallback")
    except asyncio.TimeoutError:
        logger.debug("[run.py] Dynamic plan generation timed out (>10s), using fallback")
    except Exception as e:
        logger.debug("[run.py] Dynamic plan generation failed: %s, using fallback", e)

    return list(FALLBACK_INITIAL_TASKS)


def cmd_run(
    objective: str,
    background: bool = False,
    dry_run: bool = False,
    swarm: bool = False,
    no_plan: bool = False,
    model: Optional[str] = None,
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
        if model:
            cmd_args.extend(["--model", model])
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

        active_model = resolve_active_model_string(model)
        gateway = get_gateway(active_model)
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
                        tasks = await generate_initial_plan_tasks(objective, gateway, active_model)
                        init_plan(
                            objective=objective,
                            tasks=tasks,
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
            model=active_model,
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

    from aja.runtime.workspace_lock import WorkspaceLock, WorkspaceLockError

    try:
        with WorkspaceLock(objective=objective):
            asyncio.run(_run_harness())
    except WorkspaceLockError as e:
        print_error(str(e))
        raise SystemExit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Mission interrupted by user.[/]")
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception as e:
        print_error(f"Harness Execution Error: {e}")
        raise SystemExit(1) from e

