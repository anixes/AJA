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
load_dotenv(override=True)

from aja.config import PROJECT_ROOT, DATA_DIR
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
CONFIG_PATH = DATA_DIR / "aja.json"

AGENT_MODE = False

def parse_frontmatter_meta(file_path: Path) -> dict:
    if not file_path.exists():
        return {}
    try:
        content = file_path.read_text(encoding="utf-8")
        import re
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if match:
            metadata = {}
            for line in match.group(1).split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    metadata[k.strip()] = v.strip().strip('"').strip("'")
            return metadata
    except Exception:
        pass
    return {}
if not CONFIG_PATH.exists() and (PROJECT_ROOT / "aja.json").exists():
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
    baton_dir = DATA_DIR / "batons"
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

    if AGENT_MODE:
        output = {
            "mode": mode,
            "batons": batons,
            "tasks": [
                {
                    "id": str(t.get("id", "")),
                    "status": t.get("status", ""),
                    "input": t.get("input", ""),
                    "updated_at": t.get("updated_at", "-"),
                }
                for t in tasks
            ]
        }
        print(json.dumps(output, indent=2), flush=True)
        return

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
        "[bold cyan][Agent] AJA:[/] Greetings. I am AJA, your Assistant of Joint Agents. How can I assist you today?"
    )
    console.print(
        "[dim]Tip: Use Alt+Enter for multiline input. Type '/' for commands.[/]"
    )

    # Slash command completer
    completer = WordCompleter(
        [
            "/swarm",
            "/goal",
            "/schedule",
            "/status",
            "/doctor",
            "/mode",
            "/models",
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
        history=FileHistory(str(DATA_DIR / ".aja_history")),
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
                engine = "Agent"
                tasks = f"Tasks: {p} pending, {r} running"
                health = "Health: [green]OK[/green]"
                return HTML(
                    f' <style bg="ansicyan" fg="ansiblack"> <b>AJA</b> </style> | Engine: {engine} | {tasks} | {health} '
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
                elif cmd == "/models":
                    if args:
                        parts = args.split()
                        p_model = parts[0]
                        w_model = parts[1] if len(parts) > 1 else parts[0]
                    else:
                        from aja.config import AJA_PLANNER_MODEL, AJA_WORKER_MODEL
                        console.print(f"\n[bold cyan]Engine: Swarm Agents (Planner):[/] {AJA_PLANNER_MODEL}")
                        console.print(f"[bold cyan]Engine: Single Agent (Worker):[/] {AJA_WORKER_MODEL}")
                        console.print("[dim]Tip: Swarm Planner manages complex project breakdowns (use smart models). Single Agent Worker executes hands-on tools (use fast models).[/dim]\n")
                        choices_map = {
                            "1": "copilot:gpt-4o",
                            "2": "copilot:gpt-4o-mini",
                            "3": "copilot:gpt-5.4-mini",
                            "4": "copilot:gpt-5-mini",
                            "5": "copilot:gpt-5.4",
                            "6": "copilot:gpt-5.2",
                            "7": "copilot:gpt-5.3-codex",
                            "8": "copilot:gpt-5.5",
                            "9": "copilot:claude-haiku-4.5",
                            "10": "copilot:claude-sonnet-4.5",
                            "11": "copilot:claude-sonnet-4.6",
                            "12": "copilot:claude-opus-4.7",
                            "13": "copilot:claude-opus-4.8",
                            "14": "copilot:gemini-3.5-flash",
                            "15": "Custom / Type your own",
                            "16": "Cancel"
                        }
                        
                        console.print("[bold]Select new models from Copilot:[/bold]")
                        for k, v in choices_map.items():
                            console.print(f"  {k}) {v}")
                            
                        from rich.prompt import Prompt
                        p_choice = Prompt.ask("\nSelect [bold cyan]Swarm Planner[/] option", choices=list(choices_map.keys()), default="16")
                        
                        if p_choice == "16":
                            continue
                        elif p_choice == "15":
                            p_model = Prompt.ask("Enter Planner model (e.g. copilot:gpt-4o)")
                        else:
                            p_model = choices_map[p_choice]
                            
                        w_choice = Prompt.ask("Select [bold cyan]Single Agent Worker[/] option (Press Enter to use same)", choices=list(choices_map.keys()) + [""], default="")
                        
                        if w_choice == "" or w_choice == p_choice:
                            w_model = p_model
                        elif w_choice == "16":
                            continue
                        elif w_choice == "15":
                            w_model = Prompt.ask("Enter Worker model (e.g. copilot:gpt-4o-mini)")
                        else:
                            w_model = choices_map[w_choice]
                        
                    cfg_path = DATA_DIR / "aja.json"
                    data = {}
                    if cfg_path.exists():
                        with open(cfg_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    
                    if "swarm_settings" not in data:
                        data["swarm_settings"] = {}
                    if "models" not in data["swarm_settings"]:
                        data["swarm_settings"]["models"] = {}
                    
                    data["swarm_settings"]["models"]["planner"] = p_model
                    data["swarm_settings"]["models"]["worker"] = w_model
                    
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=4)
                    
                    # Update live runtime config
                    import aja.config
                    aja.config.AJA_PLANNER_MODEL = p_model
                    aja.config.AJA_WORKER_MODEL = w_model
                    
                    console.print(f"[green]Successfully updated models![/green]")
                    console.print(f"[bold cyan]Engine: Swarm Agents (Planner):[/] {p_model}")
                    console.print(f"[bold cyan]Engine: Single Agent (Worker):[/] {w_model}")
                    continue
                elif cmd == "/swarm":
                    console.print(
                        f"[bold magenta]🚀 [Swarm] Executing mission: {args}[/bold magenta]"
                    )
                    cmd_run(args)
                    continue
                elif cmd == "/goal":
                    if args:
                        console.print(f"[bold magenta]🚀 [Goal] Executing persistent background mission: {args}[/bold magenta]")
                        cmd_run(args, background=True)
                    else:
                        console.print("[red]Usage: /goal <objective>[/red]")
                    continue
                elif cmd == "/schedule":
                    from rich.prompt import Prompt
                    objective = args
                    if not objective:
                        objective = Prompt.ask("Enter objective for the scheduled task")
                    if not objective:
                        continue
                    expr = Prompt.ask("Enter schedule expression (e.g., 'every 2h', '0 0 * * *')")
                    if not expr:
                        continue
                    try:
                        from aja.scheduler.cron_scheduler import CronScheduler
                        scheduler = CronScheduler()
                        scheduler.add_job(objective, expr)
                        console.print(f"[green]Successfully scheduled task![/green]")
                        console.print(f"  [bold]Objective:[/] {objective}")
                        console.print(f"  [bold]Schedule:[/] {expr}")
                        console.print("[yellow]Note: The task will be picked up by the autonomous loop/scheduler daemon.[/yellow]")
                    except Exception as e:
                        console.print(f"[red]Failed to schedule task:[/] {e}")
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

                console.print(f"[bold cyan][Agent] AJA:[/] {intent['response']}")

                # Update conversation history
                history.append({"role": "user", "content": user_input})
                history.append(
                    {"role": "assistant", "content": intent.get("response", "")}
                )
                history = history[-15:]

                if intent["type"] == "tool_calls" and intent.get("tool_calls"):
                    console.print(f"[*] Executing {len(intent['tool_calls'])} tool call(s)...")
                    try:
                        from aja.orchestration.tools.executor import ToolExecutor
                        from aja.observability.telemetry import get_trace_id
                        import threading
                        
                        executor = ToolExecutor()
                        box = {}
                        def thread_target():
                            box["results"] = asyncio.run(executor.dispatch_tool_calls(
                                tool_calls=intent["tool_calls"],
                                trace_id=get_trace_id(),
                            ))
                        t = threading.Thread(target=thread_target)
                        t.start()
                        t.join()
                        results = box["results"]
                        
                        for r in results:
                            if r.success:
                                console.print(f"[green]✔ Tool {r.tool} succeeded:[/]")
                                if r.data:
                                    console.print(str(r.data))
                            else:
                                err_msg = r.error or getattr(r, "stderr", None) or r.data
                                console.print(f"[red]✘ Tool {r.tool} failed: {err_msg}[/]")
                            
                            obs = f"[{r.tool}] exit={r.exit_code if r.exit_code is not None else 0}\n{r.data or r.error or getattr(r, 'stderr', '')}"
                            history.append({"role": "system", "content": obs})
                        
                        history = history[-15:]
                    except Exception as e:
                        console.print(f"[red]Failed to execute tool calls:[/] {e}")

                elif intent["type"] == "goal" and intent.get("goal"):
                    console.print(f"[yellow]Notice: To launch a full Swarm mission for complex goals, please prefix your request with /swarm (e.g., /swarm {intent['goal']})[/yellow]")

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
    import shutil

    console.print(
        Panel(
            "[bold cyan]Welcome to the AJA Setup Wizard[/]\n\n"
            "This tool will guide you through scaffolding directories, validating config keys, "
            "and setting up your local database files to ensure enterprise-grade product readiness.",
            title="AJA Onboarding",
            border_style="cyan",
        )
    )

    # Copy .env.example to .env if .env doesn't exist
    env_path = DATA_DIR / ".env"
    env_example_path = DATA_DIR / ".env.example"
    if not env_path.exists() and env_example_path.exists():
        console.print("[dim]No .env file found. Copying from .env.example...[/dim]")
        shutil.copy(env_example_path, env_path)

    # Check if config already exists
    if CONFIG_PATH.exists():
        recreate = Confirm.ask(
            "[yellow]An aja.json already exists. Re-configure?[/]", default=False
        )
        if not recreate:
            print_info("Skipping configuration generation. Verifying directories...")
            # Still initialize folders
            baton_dir = DATA_DIR / "batons"
            baton_dir.mkdir(parents=True, exist_ok=True)
            handover_dir = DATA_DIR / "handovers"
            handover_dir.mkdir(parents=True, exist_ok=True)
            print_success("Setup and directories verified.")
            return

    # Helper function to prompt for provider and model
    def ask_for_model(role_name: str):
        console.print(f"\n[bold magenta]--- Setup {role_name} ---[/bold magenta]")
        providers = {
            "1": "copilot",
            "2": "openai",
            "3": "anthropic",
            "4": "google",
            "5": "llama_cpp"
        }
        console.print("Select Provider:")
        for k, v in providers.items():
            console.print(f"  {k}) {v}")
        p_choice = Prompt.ask("Provider Option", choices=list(providers.keys()), default="1")
        provider = providers[p_choice]

        if provider == "copilot":
            models = ["gpt-4o", "gpt-4o-mini", "claude-haiku-4.5", "claude-sonnet-4.6"]
        elif provider == "openai":
            models = ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"]
        elif provider == "anthropic":
            models = ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"]
        elif provider == "google":
            models = ["gemini-2.5-flash", "gemini-2.5-pro"]
        elif provider == "llama_cpp":
            # For local, just prompt for free text
            model_name = Prompt.ask(f"Enter Local Model name for {role_name} (e.g. gemma-2-9b-it)")
            return f"{provider}:{model_name}", provider
            
        console.print(f"Select Top {provider.capitalize()} Model:")
        for i, m in enumerate(models, 1):
            console.print(f"  {i}) {m}")
        console.print(f"  {len(models) + 1}) Custom / Type your own")
        
        m_choice = Prompt.ask("Model Option", choices=[str(i) for i in range(1, len(models) + 2)], default="1")
        if int(m_choice) <= len(models):
            model_name = models[int(m_choice) - 1]
        else:
            model_name = Prompt.ask(f"Enter Custom {provider} Model name")
            
        return f"{provider}:{model_name}", provider

    # Helper function to securely update .env keys
    def update_env_key(key: str, value: str):
        if not value: return
        if not env_path.exists():
            env_path.touch()
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = [l for l in lines if not l.startswith(f"{key}=")]
        new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Prompt for configuration values
    project_name = Prompt.ask("\nEnter Project Name", default="AJA")

    operating_mode = Prompt.ask(
        "Choose Operating Mode",
        choices=["offline", "online", "hybrid"],
        default="hybrid",
    )
    
    console.print("\n[dim]The Swarm Planner orchestrates high-level tasks, while the Single Agent Worker executes individual steps.[/dim]")
    planner_model, planner_provider = ask_for_model("Swarm Planner")
    console.print("[dim]* Note: The Swarm Critic model is automatically linked to your Planner model for onboarding simplicity. Separating the roles guarantees opposing system prompts (Builder vs Attacker) for higher quality results.[/dim]")
    worker_model, worker_provider = ask_for_model("Single Agent Worker")
    
    # Handle API Keys
    console.print("\n[bold magenta]--- API Key Validation ---[/bold magenta]")
    required_providers = set([planner_provider, worker_provider])
    if "openai" in required_providers:
        if not os.environ.get("OPENAI_API_KEY"):
            val = Prompt.ask("Enter OPENAI_API_KEY (or press Enter to skip)", password=True, default="")
            update_env_key("OPENAI_API_KEY", val)
    if "anthropic" in required_providers:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            val = Prompt.ask("Enter ANTHROPIC_API_KEY (or press Enter to skip)", password=True, default="")
            update_env_key("ANTHROPIC_API_KEY", val)
    if "google" in required_providers:
        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
            val = Prompt.ask("Enter GEMINI_API_KEY (or press Enter to skip)", password=True, default="")
            update_env_key("GEMINI_API_KEY", val)
            update_env_key("GOOGLE_API_KEY", val)
            
    # Handle Integrations
    console.print("\n[bold magenta]--- Platform Integrations ---[/bold magenta]")
    if Confirm.ask("Do you want to configure a Telegram Bot token now?", default=False):
        t_token = Prompt.ask("Enter TELEGRAM_BOT_TOKEN", password=True)
        update_env_key("TELEGRAM_BOT_TOKEN", t_token)

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
            "models": {"planner": planner_model, "worker": worker_model, "critic": planner_model},
            "operating_mode": operating_mode,
        },
    }

    # Validate with Pydantic
    try:
        from aja.config_schema import AJAConfig

        AJAConfig.model_validate(config_data)

        # Write to file
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        print_success(f"\nSuccessfully generated and validated {CONFIG_PATH}")
    except Exception as e:
        print_error(f"Failed to validate generated configuration: {e}")
        return

    # Scaffold directories
    baton_dir = DATA_DIR / "batons"
    baton_dir.mkdir(parents=True, exist_ok=True)
    handover_dir = DATA_DIR / "handovers"
    handover_dir.mkdir(parents=True, exist_ok=True)
    print_success("Vector store database directories successfully initialized.")
    console.print("\n[bold green]Setup Complete! You can now run `aja chat`.[/bold green]")


def cmd_doctor(ci_mode: bool = False):
    """System health checks and diagnostics."""
    from aja.utils.diagnostics import run_diagnostics

    checks = run_diagnostics()
    
    if AGENT_MODE:
        output = {
            "status": "ok" if all(status for name, status, msg in checks) else "failed",
            "checks": [
                {
                    "name": name,
                    "passed": bool(status),
                    "message": msg
                }
                for name, status, msg in checks
            ]
        }
        print(json.dumps(output, indent=2), flush=True)
        if ci_mode:
            critical_checks = {"Native Engine", "Memory Manager", "Config Validation"}
            critical_failures = [name for name, status, msg in checks if not status and name in critical_checks]
            if critical_failures:
                sys.exit(1)
        return

    print_doctor(checks)

    if ci_mode:
        critical_checks = {"Native Engine", "Memory Manager", "Config Validation"}
        failures = [name for name, status, msg in checks if not status]
        critical_failures = [f for f in failures if f in critical_checks]
        
        if critical_failures:
            console.print(f"[bold red]CI Mode: Diagnostics failed for: {', '.join(critical_failures)}[/bold red]")
            sys.exit(1)
        elif failures:
            console.print(f"[bold yellow]CI Mode: Warnings for: {', '.join(failures)} (non-blocking)[/bold yellow]")
        else:
            console.print("[bold green]CI Mode: All diagnostics passed.[/bold green]")


def cmd_exec(args: List[str]):
    """Inspect canonical execution runtime sessions and artifacts."""
    from rich.table import Table
    from aja.runtime.execution import get_default_execution_manager

    manager = get_default_execution_manager()
    subcmd = args[0].lower() if args else "list"
    exec_root = DATA_DIR / "executions"

    if subcmd == "list":
        table = Table(title="Execution Sessions")
        table.add_column("Session")
        table.add_column("State")
        table.add_column("Started")
        table.add_column("Command")

        active = {item["session_id"]: item for item in manager.list_active()}
        seen = set()
        for session_id, item in active.items():
            seen.add(session_id)
            table.add_row(session_id, item.get("state", "unknown"), item.get("started_at") or "-", item.get("command", "")[:80])

        if exec_root.exists():
            for path in sorted(exec_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if not path.is_dir() or path.name in seen:
                    continue
                result_path = path / "result.json"
                manifest_path = path / "manifest.json"
                state = "unknown"
                started = "-"
                command = ""
                if result_path.exists():
                    try:
                        result = json.loads(result_path.read_text(encoding="utf-8"))
                        state = result.get("state", "unknown")
                        started = result.get("started_at", "-")
                    except Exception:
                        pass
                if manifest_path.exists():
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        command = manifest.get("command", "")
                    except Exception:
                        pass
                table.add_row(path.name, state, started, command[:80])
        console.print(table)
        return

    if len(args) < 2 and subcmd not in {"cleanup", "replay"}:
        print_error("Usage: aja exec <show|timeline|diff|apply|replay|cleanup> <session_id>")
        return

    if subcmd == "cleanup":
        removed = manager.cleanup_stale()
        print_success(f"Removed {len(removed)} stale execution workspaces.")
        return

    if subcmd == "replay":
        if len(args) < 2 or args[1] == "--latest":
            latest = None
            latest_time = 0
            if exec_root.exists():
                for p in exec_root.iterdir():
                    if p.is_dir():
                        mtime = p.stat().st_mtime
                        if mtime > latest_time:
                            latest_time = mtime
                            latest = p.name
            if not latest:
                print_error("No execution sessions found.")
                return
            session_id = latest
        else:
            session_id = args[1]

        session_root = exec_root / session_id
        if not session_root.exists():
            print_error(f"No execution session found: {session_id}")
            return

        from aja.tui.replay_viewer import run_replay
        run_replay(session_id, exec_root)
        return

    session_id = args[1]
    session_root = exec_root / session_id
    if subcmd == "show":
        result_path = session_root / "result.json"
        manifest_path = session_root / "manifest.json"
        if not result_path.exists() and not manifest_path.exists():
            print_error(f"No execution session found: {session_id}")
            return
        if manifest_path.exists():
            console.print_json(manifest_path.read_text(encoding="utf-8"))
        if result_path.exists():
            console.print_json(result_path.read_text(encoding="utf-8"))
        return

    if subcmd == "timeline":
        timeline = manager.get_timeline(session_id)
        for event in timeline:
            console.print(f"[dim]{event.get('timestamp', '-')}[/] [cyan]{event.get('event_type', '-')}[/] {event.get('message', '')}")
        return

    if subcmd == "diff":
        diff = manager.get_diff(session_id)
        console.print_json(json.dumps(diff, indent=2))
        return

    if subcmd == "apply":
        diff = manager.get_diff(session_id)
        if not diff.get("diff_text"):
            print_error(f"No patch diff found for session {session_id} or no changes were made.")
            return

        patch_text = diff["diff_text"]
        patch_file = session_root / "apply.patch"
        patch_file.write_text(patch_text, encoding="utf-8")

        console.print(f"[*] Validating patch for session [bold]{session_id}[/bold]...")
        res_check = subprocess.run(
            ["git", "apply", "--check", "--binary", str(patch_file)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True
        )
        if res_check.returncode != 0:
            print_error(f"Patch validation failed:\n{res_check.stderr or res_check.stdout}")
            return

        console.print("[green][*] Validation passed. Applying patch to project root...[/green]")
        res_apply = subprocess.run(
            ["git", "apply", "--binary", str(patch_file)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True
        )
        if res_apply.returncode == 0:
            print_success(f"Successfully applied isolated workspace changes for session {session_id}.")
        else:
            print_error(f"Failed to apply patch:\n{res_apply.stderr or res_apply.stdout}")
        return

    print_error(f"Unknown exec command: {subcmd}")


def show_help():
    """Displays the AJA Command Suite."""
    if AGENT_MODE:
        rules = []
        skills = []
        
        brief_text = "AJA Orchestration Engine"
        brief_path = PROJECT_ROOT / "agent" / "brief.md"
        if brief_path.exists():
            brief_text = brief_path.read_text(encoding="utf-8").strip()
            
        rules_dir = PROJECT_ROOT / "agent" / "rules"
        if rules_dir.exists():
            for p in rules_dir.glob("*.md"):
                meta = parse_frontmatter_meta(p)
                rules.append({
                    "name": meta.get("name", p.stem),
                    "description": meta.get("description", "AJA trigger/workflow constraint rules file.")
                })
                
        skills_dir = PROJECT_ROOT / "agent" / "skills"
        if skills_dir.exists():
            for p in skills_dir.glob("*.md"):
                meta = parse_frontmatter_meta(p)
                skills.append({
                    "name": meta.get("name", p.stem),
                    "description": meta.get("description", "AJA extended skills documentation.")
                })
                
        help_json = {
            "help": brief_text,
            "commands": [
                {
                    "name": "run",
                    "description": "Start an autonomous mission with the given objective.",
                    "parameters": [
                        {"name": "<objective>", "type": "string", "required": True},
                        {"name": "--dry-run", "type": "boolean", "required": False, "description": "Run simulation without making mutations"},
                        {"name": "--bg", "type": "boolean", "required": False, "description": "Run in background process group"}
                    ]
                },
                {"name": "chat", "description": "Launch the interactive conversational assistant loop."},
                {"name": "status", "description": "Show active swarm health, batons, and pending tasks."},
                {"name": "doctor", "description": "Run environment readiness and diagnostics checks."},
                {
                    "name": "pickup",
                    "description": "Resume a mission from a high-performance Arrow Baton code.",
                    "parameters": [
                        {"name": "<code>", "type": "string", "required": True}
                    ]
                },
                {"name": "tui", "description": "Run the live terminal curses TUI dashboard."},
                {"name": "rebuild-projections", "description": "Rebuild derived LanceDB projections from append-only journals."}
            ],
            "rules": rules if rules else [
                {"name": "trigger", "description": "When should an agent use this tool"},
                {"name": "workflow", "description": "Step-by-step usage flow"},
                {"name": "writeback", "description": "How to write feedback back"}
            ],
            "skills": skills if skills else [
                {"name": "getting-started", "description": "Technical onboarding guide to write durable activities"}
            ]
        }
        print(json.dumps(help_json, indent=2), flush=True)
        return

    from rich.panel import Panel

    help_text = """
[bold cyan]Core Mission Commands[/]
[green]swarm[/] <objective> [--dry-run] → Start a mission (with optional simulation)
[green]chat[/]              → Interactive conversational loop
[green]status[/]            → Show swarm health
[green]pickup[/] <code>      → Resume a mission
[green]tui[/] [--dry-run]     → Run premium live HTN dashboard

[bold cyan]System Commands[/]
[yellow]setup[/]              → Onboarding setup wizard
[yellow]mode[/] <mode>        → Set mode (offline/online/hybrid)
[yellow]doctor[/]             → Run diagnostics
[yellow]metrics[/]            → View performance
[yellow]exec[/] <cmd>          → Inspect execution sessions, timelines, and diffs
[yellow]mcp reload[/] <server> → Reload MCP server tools
[yellow]rebuild-projections[/] → Rebuild derived LanceDB read projections
    """
    console.print(Panel(help_text, title="AJA Command Suite", border_style="cyan"))


def cmd_rebuild_projections():
    """
    Rebuild derived LanceDB read projections from append-only journals.
    """
    print_info("Rebuilding derived LanceDB projections from append-only journals...")
    
    # Rebuild mission projections
    try:
        from aja.runtime.mission_journal import rebuild_all_mission_projections
        rebuild_all_mission_projections()
        print_success("Mission read-projections successfully rebuilt.")
    except Exception as e:
        print_error(f"Failed to rebuild mission projections: {e}")
        
    # Rebuild scheduler projections
    try:
        from aja.runtime.scheduler_journal import rebuild_scheduler_projections
        rebuild_scheduler_projections()
        print_success("Scheduler read-projections successfully rebuilt.")
    except Exception as e:
        print_error(f"Failed to rebuild scheduler projections: {e}")


def cmd_mcp(args: List[str]):
    subcmd = args[0].lower() if args else ""
    if not subcmd or subcmd not in ("reload", "install"):
        print_error("Usage: aja mcp [reload <server_id> | install <server_name>]")
        return

    if len(args) < 2:
        print_error(f"Usage: aja mcp {subcmd} <target>")
        return

    target = args[1]

    if subcmd == "reload":
        from aja.api.mcp_client import get_default_mcp_manager
        from aja.orchestration.tools.native import NativeToolRegistry

        async def _reload():
            manager = get_default_mcp_manager()
            await manager.boot_from_config()
            tools = await manager.reload(target)
            NativeToolRegistry.clear_external_schemas(prefix=f"mcp.{target}.")
            NativeToolRegistry.register_mcp_tools(manager)
            return tools

        try:
            tools = asyncio.run(_reload())
            print_success(f"Reloaded MCP server '{target}' with {len(tools)} tool(s).")
        except Exception as e:
            print_error(f"Failed to reload MCP server '{target}': {e}")

    elif subcmd == "install":
        from aja.mcp import install_mcp_server
        try:
            install_mcp_server(target)
            print_success(f"Successfully installed and configured MCP server '{target}' in aja.json.")
        except Exception as e:
            print_error(f"Failed to install MCP server '{target}': {e}")


def run_chat_with_gateway():
    """Wrapper that boots the background components concurrently with the interactive CLI chat."""
    gateway_proc = None
    worker_proc = None
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    
    if token:
        try:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            console.print("[dim][*] Booting AJA Telegram Gateway & Autonomous Worker in the background...[/]")
            gateway_proc = subprocess.Popen(
                [sys.executable, "-m", "aja.gateway.server"],
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            worker_proc = subprocess.Popen(
                [sys.executable, "-m", "aja.runtime.autonomous_loop"],
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            console.print(f"[yellow]⚠️ Warning: Failed to boot background agents: {e}[/]")
            
    try:
        cmd_chat()
    finally:
        if gateway_proc:
            try:
                gateway_proc.terminate()
                gateway_proc.wait(timeout=2)
            except Exception:
                try:
                    gateway_proc.kill()
                except Exception:
                    pass
        if worker_proc:
            try:
                worker_proc.terminate()
                worker_proc.wait(timeout=2)
            except Exception:
                try:
                    worker_proc.kill()
                except Exception:
                    pass


# ---------------------------------------------------------------------------


def main():
    global AGENT_MODE
    args = sys.argv[1:]
    
    # Intercept --brief
    if "--brief" in args:
        brief_path = PROJECT_ROOT / "agent" / "brief.md"
        if brief_path.exists():
            print(brief_path.read_text(encoding="utf-8").strip(), flush=True)
        else:
            print("AJA Orchestration Engine", flush=True)
        sys.exit(0)

    # Process explicit agent/human mode flags
    has_agent = "--agent" in args
    has_human = "--human" in args
    
    # Remove spec flags from command-line arguments list
    args = [a for a in args if a not in ("--agent", "--human")]
    
    if has_agent:
        AGENT_MODE = True
    elif has_human:
        AGENT_MODE = False
    else:
        # Default to agent mode (JSON) if stdout is piped or redirected, else human mode
        AGENT_MODE = not sys.stdout.isatty()

    if not args:
        run_chat_with_gateway()  # Default to chat for "modern" feel
        return

    cmd = args[0].lower()

    if cmd == "run":
        bg = "--bg" in args
        dry_run = "--dry-run" in args
        objective_parts = [a for a in args[1:] if a not in ("--bg", "--dry-run")]
        objective = " ".join(objective_parts)
        cmd_run(objective, background=bg, dry_run=dry_run)
    elif cmd == "chat":
        run_chat_with_gateway()
    elif cmd == "status":
        cmd_status()
    elif cmd == "setup":
        cmd_setup()
    elif cmd == "doctor":
        ci_mode = "--ci" in args
        cmd_doctor(ci_mode=ci_mode)
    elif cmd == "exec":
        cmd_exec(args[1:])
    elif cmd == "mcp":
        cmd_mcp(args[1:])
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
    elif cmd == "rebuild-projections":
        cmd_rebuild_projections()
    elif cmd == "help" or cmd == "--help" or cmd == "-h":
        show_help()
    else:
        print_error(f"Unknown command: '{cmd}'")
        show_help()


if __name__ == "__main__":
    main()
