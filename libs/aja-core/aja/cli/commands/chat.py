"""
AJA CLI Command: chat
=====================
Conversational interactive chat loop with Modern Design System & Power TUI.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import typer

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from aja.config import DATA_DIR, PROJECT_ROOT, AJA_WORKER_MODEL, AJA_PLANNER_MODEL
from aja.interface.modern import (
    console,
    print_banner,
    print_error,
    render_agent_card,
    render_tool_badge,
    render_help_grid,
)
from aja.tui.terminal import run_fullscreen_modal
from aja.tui.kanban import live_kanban
from aja.tui.tasks import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    TaskManager,
)


def cmd_chat():
    """Conversational interactive chat loop with Modern Design System & Power TUI."""
    from aja.cli.commands.doctor import cmd_doctor
    from aja.cli.commands.status import cmd_status, run_gpu_check, run_logs_check
    from aja.interface.intent_parser import parse_intent, local_router_fallback
    from aja.presence.state import get_system_state

    print_banner()
    console.print(
        render_agent_card(
            "Greetings, Operator. I am **AJA**, your Assistant of Joint Agents.\n"
            "Ready to execute system tasks, run autonomous swarms, or manage workflows.",
            model=AJA_WORKER_MODEL,
        )
    )
    console.print(
        "[dim]Tip: Use Alt+Enter for multiline input. Type '/' for command palette.[/]\n"
    )

    completer = WordCompleter(
        [
            "/kanban",
            "/tui",
            "/swarm",
            "/goal",
            "/schedule",
            "/status",
            "/doctor",
            "/models",
            "/help",
            "/clear",
            "/exit",
            "/todo",
            "/doing",
            "/done",
            "/failed",
            "/rmtask",
        ],
        ignore_case=True,
    )

    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    session = PromptSession(
        history=FileHistory(str(DATA_DIR / ".aja_history")),
        completer=completer,
        auto_suggest=AutoSuggestFromHistory(),
        key_bindings=kb,
        style=Style.from_dict(
            {
                "bottom-toolbar": "#ffffff bg:#161b22",
                "completion-menu.completion": "bg:#008888 #ffffff",
                "completion-menu.completion.current": "bg:#00aaaa #000000",
            }
        ),
    )

    task_manager = TaskManager()
    history = []

    while True:
        try:
            pending_count, running_count = task_manager.get_counts()

            def get_toolbar(p=pending_count, r=running_count):
                engine = "Agent (Fast)"
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

            if user_input.startswith("/"):
                cmd_parts = user_input.split(" ", 1)
                cmd = cmd_parts[0].lower()
                args = cmd_parts[1] if len(cmd_parts) > 1 else ""

                if cmd == "/exit":
                    console.print(
                        "[bold cyan]AJA:[/] Farewell, Operator. Standing by for next mission."
                    )
                    break
                elif cmd == "/clear":
                    console.clear()
                    print_banner()
                    continue
                elif cmd == "/help":
                    help_cmds = [
                        ("/kanban or /live", "Launch interactive full-screen Kanban task board"),
                        ("/tui", "Open Mission Control 4-tab dashboard"),
                        ("/swarm <goal>", "Decompose and execute goal with multi-agent swarm"),
                        ("/goal <goal>", "Dispatch goal to background worker"),
                        ("/schedule", "Schedule recurring background task"),
                        ("/doctor", "Run system environment diagnostics"),
                        ("/status", "Display active batons and task metrics"),
                        ("/models", "Interactive Copilot / LLM model selector"),
                        ("/todo <task>", "Add a new mission task"),
                        ("/doing <id>", "Move task to RUNNING"),
                        ("/done <id>", "Move task to COMPLETED"),
                        ("/failed <id>", "Mark task as FAILED"),
                        ("/rmtask <id>", "Delete task from board"),
                        ("/clear", "Clear terminal screen"),
                        ("/exit", "Exit AJA session"),
                    ]
                    console.print(render_help_grid(help_cmds))
                    continue
                elif cmd in ("/kanban", "/live"):
                    run_fullscreen_modal(live_kanban)
                    continue
                elif cmd == "/tui":
                    from aja.tui.curses_tui import run_curses_tui_main

                    run_fullscreen_modal(lambda: asyncio.run(run_curses_tui_main()))
                    continue
                elif cmd == "/todo":
                    if args:
                        tid = task_manager.add_task(args)
                        console.print(f"[green]✔ Added task {tid}: {args}[/green]")
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
                        console.print(f"[green]✔ Task {args} moved to COMPLETED[/green]")
                    else:
                        console.print("[red]Usage: /done <task_id>[/red]")
                    continue
                elif cmd == "/failed":
                    if args:
                        task_manager.update_status(args, STATUS_FAILED)
                        console.print(
                            f"[bold red]✘ Task {args} marked as FAILED[/bold red]"
                        )
                    else:
                        console.print("[red]Usage: /failed <task_id>[/red]")
                    continue
                elif cmd == "/rmtask":
                    if args:
                        task_manager.delete_task(args)
                        console.print(f"[dim]Task {args} deleted[/dim]")
                    else:
                        console.print("[red]Usage: /rmtask <task_id>[/red]")
                    continue
                elif cmd == "/status":
                    cmd_status()
                    continue
                elif cmd == "/models":
                    if args:
                        parts = args.split()
                        p_model = parts[0]
                        w_model = parts[1] if len(parts) > 1 else parts[0]
                    else:
                        console.print(
                            f"\n[bold cyan]Engine: Swarm Agents (Planner):[/] {AJA_PLANNER_MODEL}"
                        )
                        console.print(
                            f"[bold cyan]Engine: Single Agent (Worker):[/] {AJA_WORKER_MODEL}"
                        )
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
                            "16": "Cancel",
                        }

                        console.print("[bold]Select new models from Copilot:[/bold]")
                        for k, v in choices_map.items():
                            console.print(f"  {k}) {v}")

                        from rich.prompt import Prompt

                        p_choice = Prompt.ask(
                            "\nSelect [bold cyan]Swarm Planner[/] option",
                            choices=list(choices_map.keys()),
                            default="16",
                        )

                        if p_choice == "16":
                            continue
                        elif p_choice == "15":
                            p_model = Prompt.ask(
                                "Enter Planner model (e.g. copilot:gpt-4o)"
                            )
                        else:
                            p_model = choices_map[p_choice]

                        w_choice = Prompt.ask(
                            "Select [bold cyan]Single Agent Worker[/] option (Press Enter to use same)",
                            choices=list(choices_map.keys()) + [""],
                            default="",
                        )

                        if w_choice == "" or w_choice == p_choice:
                            w_model = p_model
                        elif w_choice == "16":
                            continue
                        elif w_choice == "15":
                            w_model = Prompt.ask(
                                "Enter Worker model (e.g. copilot:gpt-4o-mini)"
                            )
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

                    import aja.config

                    aja.config.AJA_PLANNER_MODEL = p_model
                    aja.config.AJA_WORKER_MODEL = w_model

                    console.print(f"[green]✔ Successfully updated models![/green]")
                    console.print(
                        f"[bold cyan]Engine: Swarm Agents (Planner):[/] {p_model}"
                    )
                    console.print(
                        f"[bold cyan]Engine: Single Agent Worker:[/] {w_model}"
                    )
                    continue
                elif cmd == "/swarm":
                    if args:
                        console.print(
                            f"[bold magenta]🚀 [Swarm] Executing adaptive multi-agent mission: {args}[/bold magenta]"
                        )
                        from aja.orchestration.goal_session import GoalSwarmSession

                        sys_state = get_system_state()
                        dry_run = (
                            sys_state.get("dry_run", False)
                            if isinstance(sys_state, dict)
                            else False
                        )
                        asyncio.run(GoalSwarmSession(dry_run=dry_run).run(args))
                    else:
                        console.print("[red]Usage: /swarm <objective>[/red]")
                    continue
                elif cmd == "/goal":
                    if args:
                        console.print(
                            f"[bold magenta]🚀 [Goal] Executing persistent direct mission: {args}[/bold magenta]"
                        )
                        from aja.orchestration.goal_session import GoalSession

                        sys_state = get_system_state()
                        dry_run = (
                            sys_state.get("dry_run", False)
                            if isinstance(sys_state, dict)
                            else False
                        )
                        asyncio.run(GoalSession(dry_run=dry_run).run(args))
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
                    expr = Prompt.ask(
                        "Enter schedule expression (e.g., 'every 2h', '0 0 * * *')"
                    )
                    if not expr:
                        continue
                    try:
                        from aja.scheduler.cron_scheduler import CronScheduler

                        scheduler = CronScheduler()
                        scheduler.add_job(objective, expr)
                        console.print(f"[green]✔ Successfully scheduled task![/green]")
                        console.print(f"  [bold]Objective:[/] {objective}")
                        console.print(f"  [bold]Schedule:[/] {expr}")
                        console.print(
                            "[yellow]Note: The task will be picked up by the autonomous loop/scheduler daemon.[/yellow]"
                        )
                    except Exception as e:
                        console.print(f"[red]Failed to schedule task:[/] {e}")
                    continue
                elif cmd == "/doctor":
                    cmd_doctor()
                    continue
                else:
                    console.print(f"[red]Unknown command: {cmd}[/red]")
                    continue

            # Sub-millisecond Fast Path check (bypasses spinner & cloud roundtrip)
            fast_intent = local_router_fallback(user_input)
            if fast_intent is not None:
                intent = fast_intent
                console.print(render_agent_card(intent["response"], model="reflex"))
            else:
                with console.status("[bold cyan]AJA is thinking...[/]"):
                    state = get_system_state()
                    intent = parse_intent(user_input, history, system_state=state)
                    console.print(render_agent_card(intent["response"], model=AJA_WORKER_MODEL))

            history.append({"role": "user", "content": user_input})
            history.append(
                {"role": "assistant", "content": intent.get("response", "")}
            )
            history = history[-15:]

            if intent["type"] == "tool_calls" and intent.get("tool_calls"):
                console.print(
                    f"[dim][*] Executing {len(intent['tool_calls'])} tool call(s)...[/dim]"
                )
                try:
                    from aja.observability.telemetry import get_trace_id
                    from aja.orchestration.tools.executor import ToolExecutor

                    executor = ToolExecutor()
                    t0 = time.time()
                    results = asyncio.run(
                        executor.dispatch_tool_calls(
                            tool_calls=intent["tool_calls"],
                            trace_id=get_trace_id(),
                        )
                    )
                    elapsed_ms = (time.time() - t0) * 1000

                    for r in results:
                        err_msg = r.error or getattr(r, "stderr", None)
                        console.print(
                            render_tool_badge(
                                tool_name=r.tool,
                                success=r.success,
                                execution_ms=elapsed_ms,
                                data=str(r.data) if r.data else None,
                                error=err_msg,
                            )
                        )

                        obs = f"[{r.tool}] exit={r.exit_code if r.exit_code is not None else 0}\n{r.data or r.error or getattr(r, 'stderr', '')}"
                        history.append({"role": "system", "content": obs})

                    history = history[-15:]
                except Exception as e:
                    console.print(f"[red]Failed to execute tool calls:[/] {e}")

            elif intent["type"] == "goal" and intent.get("goal"):
                from aja.orchestration.plan_gate import plan_gate

                try:
                    processed_goal = asyncio.run(plan_gate(intent["goal"]))
                except typer.Exit:
                    continue
                except Exception as e:
                    console.print(f"[dim]Plan gate check skipped: {e}[/dim]")
                    processed_goal = intent["goal"]

                console.print(
                    f"[bold magenta]🚀 [Direct] Transitioning to Direct Execution for goal...[/bold magenta]"
                )
                from aja.orchestration.direct_session import DirectSession

                ds = DirectSession()
                try:
                    asyncio.run(
                        ds._turn(processed_goal, console, interactive=False)
                    )
                except Exception as e:
                    console.print(f"[red]Direct Execution failed: {e}[/red]")

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


def run_chat_with_gateway():
    """Wrapper that boots background components concurrently with interactive CLI chat."""
    gateway_proc = None
    worker_proc = None
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")

    if token:
        try:
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            )
            console.print(
                "[dim][*] Booting AJA Telegram Gateway & Autonomous Worker in the background...[/dim]"
            )
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
            console.print(
                f"[yellow]⚠️ Warning: Failed to boot background agents: {e}[/]"
            )

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
