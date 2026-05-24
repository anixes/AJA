"""
AJA — Unified CLI Entry Point
=================================
The central nervous system of the AJA swarm.
Now with a modern, premium CLI experience.
"""

import sys
import os
import json
import asyncio
import subprocess
import time
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
from rich.prompt import Confirm
from aja.tui.tasks import (
    TaskManager,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from aja.tui.kanban import render_kanban_board

# prompt_toolkit for Power CLI
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.key_binding import KeyBindings

# Load environment secrets
load_dotenv()

from aja.config import PROJECT_ROOT
from aja.runtime.handover import BatonManager
from aja.interface.modern import (
    console,
    print_banner,
    print_status,
    print_doctor,
    mission_spinner,
    print_error,
    print_success,
    print_info,
)

PYTHON = sys.executable
CONFIG_PATH = PROJECT_ROOT / "aja.json"

# ---------------------------------------------------------------------------
# Core Commands
# ---------------------------------------------------------------------------


def cmd_run(objective: str, background: bool = False, dry_run: bool = False):
    """
    Primary mission entry point.
    """
    if not objective:
        print_error("No mission objective provided.")
        return

    if background:
        print_info(f"Dispatching mission to background: {objective}")
        cmd_args = [PYTHON, "-m", "aja", "run", objective]
        if dry_run:
            cmd_args.append("--dry-run")
        subprocess.Popen(
            cmd_args,
            start_new_session=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        return

    with mission_spinner(objective):
        from aja.orchestration.swarm import SwarmEngine

        engine = SwarmEngine(dry_run=dry_run)
        try:
            asyncio.run(engine.plan_and_execute_batons(objective))
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠ Mission interrupted by user.[/]")
        except Exception as e:
            print_error(f"Swarm Execution Error: {e}")


def cmd_pickup(code: str):
    """
    Resume a mission from a high-performance Arrow Baton.
    """
    if not code:
        print_error("No baton code provided.")
        return

    print_info(f"Picking up mission baton: {code}")
    from aja.runtime.handover import BatonManager
    from aja.orchestration.swarm import SwarmEngine

    mgr = BatonManager()
    state = mgr.pickup(code)

    if not state:
        print_error(
            f"Failed to pick up baton: {code}. It may have expired or does not exist."
        )
        return

    print_success(f"Baton verified. Resuming objective: {state['objective']}")

    # In a real swarm, this would re-initialize the engine with the picked-up state
    engine = SwarmEngine()
    # For now, we simulate the resumption
    console.print(
        f"[bold cyan]AJA:[/] Resuming mission logic for: [italic]{state['objective']}[/italic]"
    )
    # asyncio.run(engine.resume_from_state(state))


def cmd_status():
    """Real-time overview of swarm health and active batons."""
    from aja.memory.manager import get_memory_manager

    mgr = get_memory_manager()

    # Mode Check
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
            mode = cfg.get("swarm_settings", {}).get("operating_mode", "OFFLINE")
    except Exception:
        mode = "UNKNOWN"

    # Active Batons
    batons = []
    baton_dir = PROJECT_ROOT / ".aja" / "batons"
    if baton_dir.exists():
        for b in baton_dir.glob("*.json"):
            try:
                with open(b, "r") as f:
                    data = json.load(f)
                    batons.append(
                        {
                            "id": b.stem,
                            "objective": data.get("objective", "Unknown"),
                            "updated_at": data.get("updated_at", "-"),
                        }
                    )
            except Exception as e:
                print(f"[!] Error reading state: {e}")
                # Fallback to defaults

    # Recent Tasks from Arrow
    tasks = []
    try:
        from aja.persistence.tasks import fetch_pending_tasks

        tasks = fetch_pending_tasks(limit=5)
    except Exception:
        pass

    print_status(mode, batons, tasks)


def run_gpu_check():
    """
    Check active GPU diagnostics using nvidia-smi, falling back to CPU/RAM/Disk resources.
    """
    console.print("\n Telemetry & Hardware Diagnostics")
    try:
        # Try running nvidia-smi
        res = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            console.print("[green]Active GPU Diagnostics (nvidia-smi):[/]")
            console.print(res.stdout)
            return
    except Exception:
        pass

    # Fallback to general system resource diagnostics
    console.print(
        "[yellow]⚠ Specialized GPU diagnostics (nvidia-smi) unavailable or not found.[/]"
    )
    console.print("[bold cyan]System Resources Fallback Diagnostics:[/]")
    try:
        import psutil
    except ImportError:
        psutil = None

    if psutil is not None:
        try:
            cpu_count = psutil.cpu_count(logical=True)
            cpu_percent = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            total_ram_gb = ram.total / (1024**3)
            used_ram_gb = ram.used / (1024**3)
            free_ram_gb = ram.available / (1024**3)
            import shutil

            disk = shutil.disk_usage(str(PROJECT_ROOT))
            free_disk_gb = disk.free / (1024**3)
            total_disk_gb = disk.total / (1024**3)

            console.print(
                f"  [bold]Logical CPUs:[/] {cpu_count} (Current Usage: {cpu_percent}%)"
            )
            console.print(
                f"  [bold]System Memory (RAM):[/] {used_ram_gb:.1f} GB used / {total_ram_gb:.1f} GB total ({free_ram_gb:.1f} GB free)"
            )
            console.print(
                f"  [bold]Disk Space:[/] {free_disk_gb:.1f} GB free / {total_disk_gb:.1f} GB total"
            )
        except Exception as e:
            console.print(f"[red]Error querying psutil metrics: {e}[/]")
    else:
        cpu_count = os.cpu_count() or 1
        import shutil

        try:
            disk = shutil.disk_usage(str(PROJECT_ROOT))
            free_disk_gb = disk.free / (1024**3)
            total_disk_gb = disk.total / (1024**3)
            console.print(f"  [bold]Logical CPUs:[/] {cpu_count}")
            console.print(
                f"  [bold]System Memory (RAM):[/] N/A (psutil module missing)"
            )
            console.print(
                f"  [bold]Disk Space:[/] {free_disk_gb:.1f} GB free / {total_disk_gb:.1f} GB total"
            )
        except Exception as e:
            console.print(f"[red]Error querying system resources: {e}[/]")
    console.print("[bold cyan]───────────────────────────────────────[/]\n")


def run_logs_check():
    """
    Tail the last 15 lines of aja_output.log, autonomous_loop.log, and gateway.log.
    """
    log_files = ["aja_output.log", "autonomous_loop.log", "gateway.log"]
    console.print("\n Active Swarm & Gateway Logs (Last 15 Lines)")

    for filename in log_files:
        path = PROJECT_ROOT / filename
        console.print(f"\n📖 Log file: {filename}")
        if not path.exists():
            console.print("  (File does not exist yet or has no entries)")
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if not lines:
                console.print("  ](Log is empty)")
                continue
            tail = lines[-15:]
            for line in tail:
                console.print(line.rstrip())
        except Exception as e:
            console.print(f"  [red]Error reading log: {e}[/]")

    console.print("──────────────────────────────────────────────────\n")


def cmd_chat():
    """Conversational interactive chat loop with Power TUI features."""
    from aja.interface.intent_parser import parse_intent
    from aja.presence.state import get_system_state

    print_banner()
    console.print(
        "[bold cyan]AJA:[/] Greetings. I am AJA, your Assistant of Joint Agents. How can I assist you today?"
    )
    console.print(
        "[dim]Tip: Use Alt+Enter for multiline input. Type '/' for commands.[/]"
    )

    # Slash command completer
    completer = WordCompleter(
        [
            "/run",
            "/status",
            "/doctor",
            "/mode",
            "/metrics",
            "/exit",
            "/clear",
            "/help",
            "/kanban",
            "/todo",
            "/doing",
            "/done",
            "/failed",
            "/rmtask",
        ],
        ignore_case=True,
    )

    # Custom Key Bindings for Multiline
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    # Create Session
    session = PromptSession(
        history=FileHistory(str(PROJECT_ROOT / ".aja_history")),
        completer=completer,
        auto_suggest=AutoSuggestFromHistory(),
        key_bindings=kb,
        style=Style.from_dict(
            {
                "bottom-toolbar": "#ffffff bg:#222222",
                "completion-menu.completion": "bg:#008888 #ffffff",
                "completion-menu.completion.current": "bg:#00aaaa #000000",
            }
        ),
    )

    # Initialize Kanban Task Manager
    task_manager = TaskManager()
    history = []

    while True:
        try:
            # Update toolbar with optimized Arrow counts
            pending_count, running_count = task_manager.get_counts()

            def get_toolbar(p=pending_count, r=running_count):
                mode = "NORMAL"
                tasks = f"Tasks: {p} pending, {r} running"
                health = "Health: [green]OK[/green]"
                return HTML(
                    f' <style bg="ansicyan" fg="ansiblack"> <b>AGENTX</b> </style> | Mode: {mode} | {tasks} | {health} '
                )

            user_input = session.prompt(
                HTML("<cyan><b>User > </b></cyan>"), bottom_toolbar=get_toolbar
            ).strip()

            if not user_input:
                continue

            # Handle Slash Commands
            if user_input.startswith("/"):
                cmd_parts = user_input.split(" ", 1)
                cmd = cmd_parts[0].lower()
                args = cmd_parts[1] if len(cmd_parts) > 1 else ""

                if cmd == "/exit":
                    console.print(
                        "[bold cyan]AJA:[/] Farewell. Standing by for next mission."
                    )
                    break
                elif cmd == "/clear":
                    console.clear()
                    print_banner()
                    continue
                elif cmd == "/kanban":
                    render_kanban_board(task_manager)
                    continue
                elif cmd == "/live":
                    from aja.tui.kanban import live_kanban

                    live_kanban()
                    continue
                elif cmd == "/todo":
                    if args:
                        tid = task_manager.add_task(args)
                        console.print(f"[green]Added task {tid}: {args}[/green]")
                    else:
                        console.print("[red]Usage: /todo <task title>[/red]")
                    continue
                elif cmd == "/doing":
                    if args:
                        task_manager.update_status(args, STATUS_RUNNING)
                        console.print(f"[yellow]Task {args} moved to RUNNING[/yellow]")
                    else:
                        console.print("[red]Usage: /doing <task_id>[/red]")
                    continue
                elif cmd == "/done":
                    if args:
                        task_manager.update_status(args, STATUS_COMPLETED)
                        console.print(f"[green]Task {args} moved to COMPLETED[/green]")
                    else:
                        console.print("[red]Usage: /done <task_id>[/red]")
                    continue
                elif cmd == "/failed":
                    if args:
                        task_manager.update_status(args, STATUS_FAILED)
                        console.print(
                            f"[bold red]Task {args} marked as FAILED[/bold red]"
                        )
                    else:
                        console.print("[red]Usage: /failed <task_id>[/red]")
                    continue
                elif cmd == "/rmtask":
                    if args:
                        task_manager.delete_task(args)
                        console.print(f"[grey50]Task {args} deleted[/grey50]")
                    else:
                        console.print("[red]Usage: /rmtask <task_id>[/red]")
                    continue
                elif cmd == "/status":
                    cmd_status()
                    continue
                elif cmd == "/metrics":
                    console.print("[yellow]Metrics TUI coming soon in Phase 12.[/]")
                    continue
                elif cmd == "/mode":
                    console.print(
                        f"[bold cyan]AJA:[/] Current mode is set via aja.json. Use '/mode <type>' (offline/online/hybrid). [dim](Manual switch coming soon)[/]"
                    )
                    continue
                elif cmd == "/run":
                    console.print(
                        f"[bold cyan]🚀 Executing mission: {args}[/bold cyan]"
                    )
                    cmd_run(args)
                    continue
                elif cmd == "/doctor":
                    cmd_doctor()
                    continue
                else:
                    console.print(f"[red]Unknown command: {cmd}[/red]")
                    continue

            with console.status("[bold cyan]AJA is thinking...[/]"):
                state = get_system_state()
                intent = parse_intent(user_input, history, system_state=state)

                console.print(f"[bold cyan]AJA:[/] {intent['response']}")

                # Update conversation history
                history.append({"role": "user", "content": user_input})
                history.append(
                    {"role": "assistant", "content": intent.get("response", "")}
                )
                history = history[-15:]

                if intent["type"] == "goal" and intent["goal"]:
                    if Confirm.ask(
                        f"Shall I initiate mission: '[italic]{intent['goal']}[/]'?"
                    ):
                        cmd_run(intent["goal"])
                elif intent["type"] == "control" and intent["command"]:
                    console.print(
                        f"[*] Executing control command: [bold]{intent['command']}[/]"
                    )
                    if intent["command"] == "status":
                        cmd_status()
                    elif intent["command"] == "doctor":
                        cmd_doctor()
                    elif intent["command"] == "gpu":
                        run_gpu_check()
                    elif intent["command"] == "logs":
                        run_logs_check()

        except KeyboardInterrupt:
            continue
        except EOFError:
            console.print(
                "\n[bold cyan]AJA:[/] Transitioning to background. Use 'aja chat' to return."
            )
            break
        except Exception as e:
            print_error(f"Chat Error: {e}")


def cmd_setup():
    """Guided onboarding setup wizard for AJA."""
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm

    console.print(
        Panel(
            "[bold cyan]Welcome to the AJA Setup Wizard[/]\n\n"
            "This tool will guide you through scaffolding directories, validating config keys, "
            "and setting up your local database files to ensure enterprise-grade product readiness.",
            title="AJA Onboarding",
            border_style="cyan",
        )
    )

    # Check if config already exists
    if CONFIG_PATH.exists():
        recreate = Confirm.ask(
            "[yellow]An aja.json already exists. Re-configure?[/]", default=False
        )
        if not recreate:
            print_info("Skipping configuration generation. Verifying directories...")
            # Still initialize folders
            baton_dir = PROJECT_ROOT / ".aja" / "batons"
            baton_dir.mkdir(parents=True, exist_ok=True)
            handover_dir = PROJECT_ROOT / ".aja" / "handovers"
            handover_dir.mkdir(parents=True, exist_ok=True)
            print_success("Setup and directories verified.")
            return

    # Prompt for configuration values
    project_name = Prompt.ask("Enter Project Name", default="AJA")

    operating_mode = Prompt.ask(
        "Choose Operating Mode",
        choices=["offline", "online", "hybrid"],
        default="offline",
    )

    # Models defaults
    if operating_mode == "offline":
        planner_model = "llama_cpp:gemma"
        worker_model = "llama_cpp:gemma"
        critic_model = "llama_cpp:gemma"
    else:
        planner_model = "google:gemini-2.0-flash"
        worker_model = "google:gemini-2.0-flash"
        critic_model = "google:gemini-2.0-flash"

    planner = Prompt.ask("Planner Model", default=planner_model)
    worker = Prompt.ask("Worker Model", default=worker_model)
    critic = Prompt.ask("Critic Model", default=critic_model)

    # Let's write API Keys to .env if needed
    if operating_mode in ("online", "hybrid"):
        api_key = Prompt.ask(
            "Enter GEMINI_API_KEY (leave empty to keep existing or skip)",
            password=True,
            default="",
        )
        if api_key:
            env_path = PROJECT_ROOT / ".env"
            existing_lines = []
            if env_path.exists():
                existing_lines = env_path.read_text(encoding="utf-8").splitlines()

            # Remove existing GEMINI_API_KEY / GOOGLE_API_KEY to avoid duplicates
            new_lines = []
            for line in existing_lines:
                if not (
                    line.startswith("GEMINI_API_KEY=")
                    or line.startswith("GOOGLE_API_KEY=")
                ):
                    new_lines.append(line)
            new_lines.append(f"GEMINI_API_KEY={api_key}")
            new_lines.append(f"GOOGLE_API_KEY={api_key}")
            env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            print_success("Saved API keys to .env")

    # Generate config dictionary
    config_data = {
        "project_name": project_name,
        "territories": [
            {
                "path": "apps/cli-ts",
                "health_cmd": "node dist/cli.js",
                "auto_heal": True,
            },
            {
                "path": "libs/aja-core",
                "health_cmd": "python -m aja status",
                "auto_heal": False,
            },
        ],
        "swarm_settings": {
            "offline_mode": operating_mode == "offline",
            "max_agents": 5,
            "check_interval": 30,
            "models": {"planner": planner, "worker": worker, "critic": critic},
            "operating_mode": operating_mode,
        },
    }

    # Validate with Pydantic
    try:
        from aja.config_schema import AgentXConfig

        AgentXConfig.model_validate(config_data)

        # Write to file
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        print_success(f"Successfully generated and validated {CONFIG_PATH}")
    except Exception as e:
        print_error(f"Failed to validate generated configuration: {e}")
        return

    # Scaffold directories
    baton_dir = PROJECT_ROOT / ".aja" / "batons"
    baton_dir.mkdir(parents=True, exist_ok=True)
    handover_dir = PROJECT_ROOT / ".aja" / "handovers"
    handover_dir.mkdir(parents=True, exist_ok=True)
    print_success("Vector store database directories successfully initialized.")


def cmd_doctor():
    """System health checks and diagnostics."""
    from aja.utils.diagnostics import run_diagnostics

    checks = run_diagnostics()
    print_doctor(checks)


def show_help():
    """Displays the AJA Command Suite."""
    from rich.panel import Panel

    help_text = """
[bold cyan]Core Mission Commands[/]
[green]run[/] <objective> [--dry-run] → Start a mission (with optional simulation)
[green]chat[/]              → Interactive conversational loop
[green]status[/]            → Show swarm health
[green]pickup[/] <code>      → Resume a mission
[green]tui[/] [--dry-run]     → Run premium live HTN dashboard

[bold cyan]System Commands[/]
[yellow]setup[/]              → Onboarding setup wizard
[yellow]mode[/] <mode>        → Set mode (offline/online/hybrid)
[yellow]doctor[/]             → Run diagnostics
[yellow]metrics[/]            → View performance
    """
    console.print(Panel(help_text, title="AJA Command Suite", border_style="cyan"))


# ---------------------------------------------------------------------------
# Main Router
# ---------------------------------------------------------------------------


def main():
    args = sys.argv[1:]
    if not args:
        cmd_chat()  # Default to chat for "modern" feel
        return

    cmd = args[0].lower()

    if cmd == "run":
        bg = "--bg" in args
        dry_run = "--dry-run" in args
        objective_parts = [a for a in args[1:] if a not in ("--bg", "--dry-run")]
        objective = " ".join(objective_parts)
        cmd_run(objective, background=bg, dry_run=dry_run)
    elif cmd == "chat":
        cmd_chat()
    elif cmd == "status":
        cmd_status()
    elif cmd == "setup":
        cmd_setup()
    elif cmd == "doctor":
        cmd_doctor()
    elif cmd == "live":
        from aja.tui.kanban import live_kanban

        live_kanban()
    elif cmd == "ui":
        subprocess.run([PYTHON, "-m", "aja.interface.tui"])
    elif cmd == "pickup":
        if len(args) < 2:
            print_error("Usage: aja pickup <code>")
        else:
            cmd_pickup(args[1])
    elif cmd == "tui":
        dry_run = "--dry-run" in args
        from aja.tui.curses_tui import run_curses_tui_main

        asyncio.run(run_curses_tui_main(dry_run=dry_run))
    elif cmd == "help" or cmd == "--help" or cmd == "-h":
        show_help()
    else:
        print_error(f"Unknown command: '{cmd}'")
        show_help()


if __name__ == "__main__":
    main()
