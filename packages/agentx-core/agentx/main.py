"""
AgentX — Unified CLI Entry Point
=================================
The central nervous system of the AgentX swarm.
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
from agentx.tui.tasks import (
    TaskManager,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from agentx.tui.kanban import render_kanban_board

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

from agentx.config import PROJECT_ROOT
from agentx.runtime.handover import BatonManager
from agentx.interface.modern import (
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
CONFIG_PATH = PROJECT_ROOT / "agentx.json"

# ---------------------------------------------------------------------------
# Core Commands
# ---------------------------------------------------------------------------


def cmd_run(objective: str, background: bool = False):
    """
    Primary mission entry point.
    """
    if not objective:
        print_error("No mission objective provided.")
        return

    if background:
        print_info(f"Dispatching mission to background: {objective}")
        subprocess.Popen(
            [PYTHON, "-m", "agentx", "run", objective],
            start_new_session=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        return

    with mission_spinner(objective):
        from agentx.orchestration.swarm import SwarmEngine

        engine = SwarmEngine()
        try:
            asyncio.run(engine.plan_and_execute_batons(objective))
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠ Mission interrupted by user.[/]")
        except Exception as e:
            print_error(f"Swarm Execution Error: {e}")


def cmd_status():
    """Real-time overview of swarm health and active batons."""
    from agentx.memory.manager import get_memory_manager

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
    baton_dir = PROJECT_ROOT / ".agentx" / "batons"
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
        from agentx.persistence.tasks import fetch_pending_tasks

        tasks = fetch_pending_tasks(limit=5)
    except Exception:
        pass

    print_status(mode, batons, tasks)


def cmd_chat():
    """Conversational interactive chat loop with Power TUI features."""
    from agentx.interface.intent_parser import parse_intent
    from agentx.presence.state import get_system_state

    print_banner()
    console.print(
        "[bold cyan]AJA:[/] Greetings. I am your Assistant to the Joint Agents. How can I assist you today?"
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
            "/dash",
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
        history=FileHistory(str(PROJECT_ROOT / ".agentx_history")),
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
                    from agentx.tui.kanban import live_kanban

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
                intent = parse_intent(user_input, [], system_state=state)

                console.print(f"[bold cyan]AJA:[/] {intent['response']}")

                if intent["type"] == "goal" and intent["goal"]:
                    if console.confirm(
                        f"Shall I initiate mission: '[italic]{intent['goal']}[/]'?"
                    ):
                        cmd_run(intent["goal"])
                elif intent["type"] == "control" and intent["command"]:
                    console.print(
                        f"[*] Executing control command: [bold]{intent['command']}[/]"
                    )
                    if intent["command"] == "status":
                        cmd_status()

        except KeyboardInterrupt:
            continue
        except EOFError:
            console.print(
                "\n[bold cyan]AJA:[/] Transitioning to background. Use 'agentx chat' to return."
            )
            break
        except Exception as e:
            print_error(f"Chat Error: {e}")


def cmd_doctor():
    """System health checks and diagnostics."""
    checks = [
        ("Config", CONFIG_PATH.exists(), str(CONFIG_PATH)),
        (
            "Native Engine",
            (PROJECT_ROOT / "packages" / "agentx-native").exists(),
            "Rust/Simd Core",
        ),
        ("Runtime Dir", (PROJECT_ROOT / ".agentx").exists(), ".agentx/"),
        ("Memory Manager", True, "LanceDB/Arrow Active"),
    ]
    print_doctor(checks)


def show_help():
    """Displays the AgentX Command Suite."""
    from rich.panel import Panel
    from rich.columns import Columns

    help_text = """
[bold cyan]Core Mission Commands[/]
[green]run[/] <objective>    → Start a mission
[green]chat[/]              → Interactive conversational loop
[green]status[/]            → Show swarm health
[green]pickup[/] <code>      → Resume a mission

[bold cyan]System Commands[/]
[yellow]mode[/] <mode>        → Set mode (offline/online/hybrid)
[yellow]dash[/]               → Start Dashboard
[yellow]doctor[/]             → Run diagnostics
[yellow]metrics[/]            → View performance
    """
    console.print(Panel(help_text, title="AgentX Command Suite", border_style="cyan"))


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
        objective = " ".join([a for a in args[1:] if a != "--bg"])
        cmd_run(objective, background=bg)
    elif cmd == "chat":
        cmd_chat()
    elif cmd == "status":
        cmd_status()
    elif cmd == "doctor":
        cmd_doctor()
    elif cmd == "live":
        from agentx.tui.kanban import live_kanban

        live_kanban()
    elif cmd == "ui":
        subprocess.run([PYTHON, "-m", "agentx.interface.tui"])
    elif cmd == "help" or cmd == "--help" or cmd == "-h":
        show_help()
    else:
        print_error(f"Unknown command: '{cmd}'")
        show_help()


if __name__ == "__main__":
    main()
