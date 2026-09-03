"""TerminalREPL — best-in-class terminal chat surface for ConversationCore.

v2: rendering is delegated to the shared :class:`EventRenderer` pipeline
(``aja.interface.renderers``) so every surface renders identically, with
REPL-specific polish layered on top:

* ``Delta``             -> buffered streaming chunks driven through
                           ``rich.live.Live`` on real terminals (plain
                           inline echo on captured/non-terminal streams)
* ``ToolStarted``       -> spinner placeholder (terminal) / dim line
* ``ToolFinished``      -> replaces the spinner with a ✓/✗ result line
* ``ApprovalRequested`` -> inline ApprovalCard panel + y/n/a gate
* ``Error``             -> red boxed Panel with code + message
* ``Final``             -> rich Markdown panel via ``render_final()``

Keyboard shortcuts (interactive prompt_toolkit sessions):

* ``Enter``       send          * ``Tab``     slash-command autocomplete
* ``Alt+Enter``   newline       * ``/``       open command palette
* ``Ctrl+C``      interrupt     * ``Ctrl+L``  clear screen
* ``Ctrl+D``      exit

Import-time purity: heavy wiring (ConversationCore construction) is lazy;
prompt_toolkit sessions are only built when no injected input provider is
supplied, keeping mocked turns fully testable under plain pytest.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, Tuple

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from aja.core.events import (
    ApprovalRequested,
    CoreEvent,
    Delta,
    Error,
    Final,
    ToolFinished,
    ToolStarted,
)
from aja.interface.renderers import EventRenderer
from aja.messaging.envelope import InboundMessage

__all__ = ["TerminalREPL", "build_key_bindings"]

from prompt_toolkit.completion import Completer, Completion

InputProvider = Callable[[str], Awaitable[str]]
ApprovalResolver = Callable[[ApprovalRequested], Awaitable[bool]]

SLASH_COMMANDS: Tuple[str, ...] = (
    "/help",
    "/clear",
    "/exit",
    "/quit",
    "/status",
    "/kanban",
    "/live",
    "/missions",
    "/models",
    "/local",
    "/tui",
    "/swarm",
    "/goal",
    "/schedule",
    "/doctor",
    "/todo",
    "/doing",
    "/done",
    "/failed",
    "/rmtask",
)

SLASH_COMMAND_DESCRIPTIONS: Dict[str, str] = {
    "/help": "Show help and command palette",
    "/clear": "Clear the terminal screen",
    "/exit": "Quit AJA (or press Ctrl+D)",
    "/quit": "Quit AJA (or press Ctrl+D)",
    "/status": "Active batons and system metrics",
    "/pc": "Autonomous direct multi-step execution",
    "/tasks": "List mission tasks and status",
    "/skills": "List available procedural skills",
    "/review": "Review uncommitted changes and diffs",
    "/kanban": "Full-screen mission kanban dashboard",
    "/live": "Real-time activity log stream",
    "/missions": "List all active and completed missions",
    "/models": "Copilot / LLM model selector",
    "/local": "Discover and select local models (Ollama, llama.cpp, LM Studio)",
    "/tui": "Mission Control curses dashboard",
    "/swarm": "Multi-agent swarm mission",
    "/goal": "Persistent direct mission",
    "/schedule": "Schedule recurring background task",
    "/doctor": "System environment diagnostics",
    "/todo": "Add a task to the mission board",
    "/doing": "Mark task in-progress",
    "/done": "Mark task completed",
    "/failed": "Mark task failed",
    "/rmtask": "Delete task from board",
}


class SlashCompleter(Completer):
    """Live dropdown completer for prompt_toolkit that triggers on slash commands."""

    def __init__(self, commands: Dict[str, str]):
        self.commands = commands

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            word = text.split()[0] if " " not in text else ""
            if word:
                for cmd, desc in self.commands.items():
                    if cmd.startswith(word):
                        yield Completion(cmd, start_position=-len(word), display=cmd, display_meta=desc)


_HELP_TEXT = """\
**Commands**
- `/help`      — show this help
- `/clear`     — clear the terminal screen
- `/exit`      — quit AJA (or press Ctrl+D)
- `/`          — open the command palette
- `/status`    — active batons and system metrics
- `/pc <goal>` — autonomous multi-step execution loop
- `/tasks`     — active mission tasks
- `/kanban` /missions /live — full-screen mission dashboard
- `/tui`       — Mission Control curses dashboard
- `/models [planner worker]` — Copilot model selector
- `/swarm <objective>` — multi-agent swarm mission
- `/goal <objective>`  — persistent direct mission
- `/schedule`  — schedule a recurring background task
- `/doctor`    — system environment diagnostics
- `/todo <task>` · `/doing <id>` · `/done <id>` · `/failed <id>` · `/rmtask <id>` — task board

**Keys**
- `Enter`     — send message
- `Alt+Enter` — newline inside multi-line input
- `Tab`       — slash command autocomplete
- `/`         — open command palette
- `Ctrl+C`    — cancel the current turn
- `Ctrl+L`    — clear screen
- `Ctrl+D`    — exit
"""


class _ReplRenderer(EventRenderer):
    """EventRenderer with REPL-flavoured overrides (legacy mark parity)."""

    def render_tool_finished(self, name: str, success: bool, duration_ms: float) -> None:
        mark = "✔" if success else "✘"
        style = "bold green" if success else "bold red"
        line = Text()
        line.append(f"{mark} {name}", style=style)
        line.append(f" ({duration_ms:.0f}ms)", style="dim")
        self.console.print(line)

    def render_error(self, code: str, message: str, recoverable: bool = True) -> Panel:
        body = Text.assemble(
            ("Code: ", "bold"),
            (code, "bold red"),
            ("\n\n", ""),
            ("Message: ", "bold"),
            (message, ""),
            ("\n", ""),
            ("Recoverable: ", "bold"),
            ("yes" if recoverable else "no", "yellow" if recoverable else "red"),
        )
        return Panel(body, title="❌ Error", border_style="red")


def build_key_bindings(
    on_send: Callable[[Any], None],
    on_interrupt: Callable[[Any], None],
    on_eof: Callable[[Any], None],
    on_tab_complete: Callable[[Any], None],
    on_clear: Callable[[Any], None],
) -> Any:
    """Build the REPL key-binding map (kept pure/injectable for tests).

    ``on_*`` callbacks receive the prompt_toolkit event.
    """
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("enter")
    def _send(event):  # type: ignore[no-untyped-def]
        on_send(event)

    @kb.add("escape", "enter")
    def _newline(event):  # type: ignore[no-untyped-def]
        event.app.current_buffer.insert_text("\n")

    @kb.add("c-c")
    def _interrupt(event):  # type: ignore[no-untyped-def]
        on_interrupt(event)

    @kb.add("c-d")
    def _eof(event):  # type: ignore[no-untyped-def]
        on_eof(event)

    @kb.add("tab")
    def _tab(event):  # type: ignore[no-untyped-def]
        on_tab_complete(event)

    @kb.add("c-l")
    def _clear(event):  # type: ignore[no-untyped-def]
        on_clear(event)

    return kb


class TerminalREPL:
    """Interactive terminal chat loop over the conversation core."""

    def __init__(
        self,
        core: Any = None,
        console: Optional[Console] = None,
        *,
        chat_id: str = "cli-local",
        user_id: str = "operator",
        input_provider: Optional[InputProvider] = None,
        approval_resolver: Optional[ApprovalResolver] = None,
        banner: bool = True,
    ) -> None:
        self._core = core
        self._console = console or Console()
        self._renderer = _ReplRenderer(console=self._console)
        self._chat_id = chat_id
        self._user_id = user_id
        self._input_provider = input_provider
        self._approval_resolver = approval_resolver
        self._banner = banner
        self._session: Any = None  # prompt_toolkit.PromptSession, built lazily
        self._live: Optional[Any] = None  # active rich.live.Live handle
        self._stream_parts: list[str] = []
        self._needs_newline = False
        self._bg_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ #
    # Wiring
    # ------------------------------------------------------------------ #

    @property
    def core(self) -> Any:
        if self._core is None:
            self._core = self._build_default_core()
        return self._core

    @property
    def console(self) -> Console:
        return self._console

    @property
    def renderer(self) -> EventRenderer:
        return self._renderer

    @staticmethod
    def _build_default_core() -> Any:
        """Build the production ConversationCore (heavy imports stay lazy)."""
        from aja.core.conversation import ConversationCore

        def _recall(query: str):
            from aja.gateway.recall import hybrid_recall

            return hybrid_recall(query)

        gateway, tools_registry, executor = _resolve_production_stack()
        model = None
        system_prompt = None
        try:
            from aja.config import AJA_PLANNER_MODEL
            from aja.cognitive.prompts import build_system_prompt

            model = AJA_PLANNER_MODEL or None
            system_prompt = build_system_prompt()
        except Exception:  # best-effort: gateway falls back to its default model
            pass
        return ConversationCore(
            gateway=gateway,
            tools_registry=tools_registry,
            executor=executor,
            recall_fn=_recall,
            model=model,
            system_prompt=system_prompt,
        )

    def _get_session(self) -> Any:
        if self._session is None:
            try:
                from prompt_toolkit import PromptSession
                from prompt_toolkit.application import Application
            except ImportError:  # pragma: no cover - prompt_toolkit is a dep
                raise RuntimeError("prompt_toolkit is required for interactive input")

            def _send(event):  # type: ignore[no-untyped-def]
                event.app.exit(result=event.app.current_buffer.text)

            def _interrupt(event):  # type: ignore[no-untyped-def]
                event.app.exit(exception=KeyboardInterrupt())

            def _eof(event):  # type: ignore[no-untyped-def]
                event.app.exit(exception=EOFError())

            def _tab_complete(event):  # type: ignore[no-untyped-def]
                buf = event.app.current_buffer
                completed = self.complete_slash(buf.text)
                if completed != buf.text:
                    buf.text = completed
                    buf.cursor_position = len(completed)

            def _clear(event):  # type: ignore[no-untyped-def]
                out = event.app.output
                out.erase_screen()
                out.cursor_home()
                out.flush()

            kb = build_key_bindings(_send, _interrupt, _eof, _tab_complete, _clear)
            completer = SlashCompleter(SLASH_COMMAND_DESCRIPTIONS)
            self._session = PromptSession(
                multiline=True,
                key_bindings=kb,
                completer=completer,
                complete_while_typing=True,
                prompt_continuation=lambda width, line, is_last_input: " " * width,
            )
        return self._session

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #

    async def _read_input(self) -> str:
        if self._input_provider is not None:
            return await self._input_provider("you> ")
        session = self._get_session()
        return await asyncio.to_thread(session.prompt, "you> ")

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        if self._banner:
            self.print_banner()
        while True:
            try:
                raw = await self._read_input()
            except EOFError:
                break
            except KeyboardInterrupt:
                continue
            text = raw.strip()
            if not text:
                continue
            if text == "/":
                self.show_palette()
                continue
            if self._try_tab_complete_hint(text):
                continue
            command = self._handle_slash_command(text)
            if command == "exit":
                break
            if command == "handled":
                continue
            await self.run_turn(text)
        self._cancel_bg_tasks()
        self._console.print("[dim]Goodbye.[/dim]")

    def _spawn_bg(self, coro) -> None:
        """Schedule a fire-and-forget background task (slash-command dispatch)."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _cancel_bg_tasks(self) -> None:
        for task in tuple(self._bg_tasks):
            task.cancel()

    def _try_tab_complete_hint(self, text: str) -> bool:
        """Partial slash token (e.g. ``/he``) autocompletes inline."""
        completed = self.complete_slash(text)
        if text.startswith("/") and " " not in text and completed != text:
            self._console.print(f"[dim]{text} → {completed}[/dim]")
            return False  # fall through so explicit commands still resolve
        return False

    async def run_turn(self, text: str) -> None:
        """Send one message through the core and render its event stream."""
        msg = InboundMessage(
            surface="cli",
            chat_id=self._chat_id,
            user_id=self._user_id,
            text=text,
        )
        turn_task = asyncio.create_task(self._consume_events(msg))
        try:
            await asyncio.shield(turn_task)
        except asyncio.CancelledError:
            # External cancel (Ctrl+C proxy): tear down the inner turn cleanly.
            turn_task.cancel()
            try:
                await turn_task
            except BaseException:  # noqa: BLE001 - swallow the interrupted turn
                pass
            self._end_stream()
            self._console.print("\n[yellow]⚠ Turn cancelled.[/yellow]")
            raise
        except KeyboardInterrupt:
            turn_task.cancel()
            try:
                await turn_task
            except BaseException:  # noqa: BLE001 - swallow the interrupted turn
                pass
            self._end_stream()
            self._console.print("\n[yellow]⚠ Turn cancelled.[/yellow]")
        except Exception as e:
            self.render_event(Error(code="REPL_FAILED", message=f"{type(e).__name__}: {e}"))

    async def _consume_events(self, msg: InboundMessage) -> None:
        self._stream_parts.clear()
        self._renderer.reset_stream()
        async for ev in self.core.handle(msg):
            if isinstance(ev, Delta):
                self._feed_delta(ev.text)
                continue
            self._end_stream()
            if isinstance(ev, ApprovalRequested):
                await self.handle_approval(ev)
            else:
                self.render_event(ev)
        self._end_stream()

    # ------------------------------------------------------------------ #
    # Streaming (buffered Deltas through rich.live.Live)
    # ------------------------------------------------------------------ #

    def _feed_delta(self, chunk: str) -> None:
        """Buffer a Delta chunk; smooth-refresh via Live on real terminals."""
        self._needs_newline = True
        if self._console.is_terminal:
            self._stream_parts.append(chunk)
            if self._live is None:
                self._live = Live(
                    Text("".join(self._stream_parts)),
                    console=self._console,
                    refresh_per_second=16,
                    transient=False,
                )
                self._live.start()
            else:
                self._live.update(Text("".join(self._stream_parts)))
                self._live.refresh()
        else:
            self._renderer.render_delta(chunk)

    def _end_stream(self) -> None:
        """Flush/close any active Live (stream text or tool spinner)."""
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:  # best-effort: never kill the turn here
                pass
            self._live = None
        if self._needs_newline:
            self._console.print()
            self._needs_newline = False

    # ------------------------------------------------------------------ #
    # Slash commands / palette
    # ------------------------------------------------------------------ #

    def complete_slash(self, text: str) -> str:
        """Longest-common-prefix Tab completion over :data:`SLASH_COMMANDS`."""
        stripped = text.strip()
        if not stripped.startswith("/") or " " in stripped:
            return text
        matches = [c for c in SLASH_COMMANDS if c.startswith(stripped)]
        if not matches:
            return text
        prefix = matches[0]
        for m in matches[1:]:
            while not m.startswith(prefix):
                prefix = prefix[:-1]
        return text.replace(stripped, prefix, 1)

    def show_palette(self) -> None:
        """Render the slash-command palette table."""
        table = Table(title="AJA Command Palette", border_style="cyan", expand=False)
        table.add_column("Command", style="bold cyan")
        table.add_column("Action", style="dim")
        table.add_row("/help", "Show help")
        table.add_row("/clear", "Clear the terminal screen")
        table.add_row("/exit", "Quit AJA")
        table.add_row("/status", "Active batons and system metrics")
        table.add_row("/kanban · /missions · /live", "Full-screen mission dashboard")
        table.add_row("/tui", "Mission Control curses dashboard")
        table.add_row("/models [planner worker]", "Copilot / LLM model selector")
        table.add_row("/swarm <objective>", "Multi-agent swarm mission")
        table.add_row("/goal <objective>", "Persistent direct mission")
        table.add_row("/schedule", "Schedule recurring background task")
        table.add_row("/doctor", "System environment diagnostics")
        table.add_row("/todo <task>", "Add a mission task to the board")
        table.add_row("/doing <id> · /done <id> · /failed <id>", "Update task status")
        table.add_row("/rmtask <id>", "Delete task from board")
        table.add_row("/", "Open this command palette")
        self._console.print(table)

    def _handle_slash_command(self, text: str) -> str:
        stripped = text.strip()
        low = stripped.lower()
        if not low.startswith("/"):
            return ""
        parts = low.split(None, 1)
        cmd = parts[0]
        raw_args = stripped.split(None, 1)[1].strip() if len(parts) > 1 else ""
        if cmd in ("/exit", "/quit"):
            return "exit"
        if cmd == "/":
            self.show_palette()
            return "handled"
        if cmd == "/help":
            self._console.print(Panel(Markdown(_HELP_TEXT), title="AJA Help", border_style="cyan"))
            return "handled"
        if cmd == "/clear":
            self._console.clear()
            return "handled"
        if cmd in ("/kanban", "/live", "/missions"):
            self._spawn_bg(asyncio.to_thread(self._open_dashboard))
            return "handled"
        if cmd == "/tui":
            self._spawn_bg(asyncio.to_thread(self._open_curses_tui))
            return "handled"
        if cmd == "/pc":
            if not raw_args:
                self._console.print("[red]Usage: /pc <objective>[/red]")
                return "handled"
            self._spawn_bg(self._run_mission("/goal", raw_args))
            return "handled"
        if cmd in ("/status", "/tasks"):
            def _status() -> None:
                from aja.cli.commands.status import cmd_status

                cmd_status()

            self._spawn_bg(asyncio.to_thread(_status))
            return "handled"
        if cmd == "/skills":
            def _skills() -> None:
                try:
                    from aja.cognitive.skills_inventory import list_all_skills
                    skills = list_all_skills()
                except Exception:
                    skills = []
                if not skills:
                    self._console.print("[dim]No procedural skills found in ~/.aja/skills/[/dim]")
                else:
                    self._console.print(f"[bold cyan]Procedural Skills ({len(skills)}):[/bold cyan]")
                    for sk in skills:
                        name = getattr(sk, "name", str(sk))
                        desc = getattr(sk, "description", "")
                        self._console.print(f"  • [yellow]{name}[/yellow]: {desc}")

            self._spawn_bg(asyncio.to_thread(_skills))
            return "handled"
        if cmd == "/doctor":
            def _doctor() -> None:
                from aja.cli.commands.doctor import cmd_doctor

                cmd_doctor()

            self._spawn_bg(asyncio.to_thread(_doctor))
            return "handled"
        if cmd in ("/local", "/models local"):
            def _local() -> None:
                from aja.cli.commands.local_cmd import cmd_local

                cmd_local(raw_args, console=self._console)

            self._spawn_bg(asyncio.to_thread(_local))
            return "handled"
        if cmd == "/models":
            def _models() -> None:
                from aja.cli.commands.models import handle_models_command

                handle_models_command(raw_args, console=self._console)

            self._spawn_bg(asyncio.to_thread(_models))
            return "handled"
        if cmd in ("/swarm", "/goal"):
            if not raw_args:
                self._console.print(f"[red]Usage: {cmd} <objective>[/red]")
                return "handled"
            self._spawn_bg(self._run_mission(cmd, raw_args))
            return "handled"
        if cmd == "/schedule":
            self._spawn_bg(asyncio.to_thread(self._schedule_flow, raw_args))
            return "handled"
        if cmd in ("/todo", "/doing", "/done", "/failed", "/rmtask"):
            if not raw_args:
                usage = {
                    "/todo": "Usage: /todo <task title>",
                    "/doing": "Usage: /doing <task_id>",
                    "/done": "Usage: /done <task_id>",
                    "/failed": "Usage: /failed <task_id>",
                    "/rmtask": "Usage: /rmtask <task_id>",
                }[cmd]
                self._console.print(f"[red]{usage}[/red]")
                return "handled"
            self._spawn_bg(asyncio.to_thread(self._run_task_command, cmd, raw_args))
            return "handled"
        self._console.print(f"[dim]Unknown command: {cmd}[/dim]")
        return "handled"

    # ------------------------------------------------------------------ #
    # Slash-command dispatch targets
    # ------------------------------------------------------------------ #

    @staticmethod
    def _open_dashboard() -> None:
        from aja.tui.dashboard import AJADashboard
        from aja.tui.terminal import run_fullscreen_modal

        run_fullscreen_modal(AJADashboard().run)

    @staticmethod
    def _open_curses_tui() -> None:
        import asyncio as _asyncio

        from aja.tui.curses_tui import run_curses_tui_main
        from aja.tui.terminal import run_fullscreen_modal

        run_fullscreen_modal(lambda: _asyncio.run(run_curses_tui_main()))

    async def _run_mission(self, kind: str, objective: str) -> None:
        try:
            from aja.presence.state import get_system_state

            sys_state = get_system_state()
            dry_run = (
                sys_state.get("dry_run", False)
                if isinstance(sys_state, dict)
                else False
            )
            from aja.orchestration.goal_session import GoalSession, GoalSwarmSession

            session_cls = GoalSwarmSession if kind == "/swarm" else GoalSession
            await session_cls(dry_run=dry_run).run(objective)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._end_stream()
            self._console.print(f"[red]Mission error:[/] {type(e).__name__}: {e}")

    def _schedule_flow(self, objective: str) -> None:
        from rich.prompt import Prompt

        if not objective:
            objective = Prompt.ask("Enter objective for the scheduled task").strip()
        if not objective:
            return
        expr = Prompt.ask(
            "Enter schedule expression (e.g., 'every 2h', '0 0 * * *')"
        ).strip()
        if not expr:
            return
        try:
            from aja.scheduler.cron_scheduler import CronScheduler

            CronScheduler().add_job(objective, expr)
            self._console.print("[green]✔ Successfully scheduled task![/green]")
            self._console.print(f"  [bold]Objective:[/] {objective}")
            self._console.print(f"  [bold]Schedule:[/] {expr}")
            self._console.print(
                "[yellow]Note: The task will be picked up by the autonomous loop/scheduler daemon.[/yellow]"
            )
        except Exception as e:
            self._console.print(f"[red]Failed to schedule task:[/] {e}")

    def _run_task_command(self, cmd: str, args_str: str) -> None:
        from aja.tui.tasks import (
            STATUS_COMPLETED,
            STATUS_FAILED,
            STATUS_RUNNING,
            TaskManager,
        )

        manager = TaskManager()
        if cmd == "/todo":
            tid = manager.add_task(args_str)
            self._console.print(f"[green]✔ Added task {tid}: {args_str}[/green]")
        elif cmd == "/doing":
            manager.update_status(args_str, STATUS_RUNNING)
            self._console.print(f"[yellow]Task {args_str} moved to RUNNING[/yellow]")
        elif cmd == "/done":
            manager.update_status(args_str, STATUS_COMPLETED)
            self._console.print(f"[green]✔ Task {args_str} moved to COMPLETED[/green]")
        elif cmd == "/failed":
            manager.update_status(args_str, STATUS_FAILED)
            self._console.print(
                f"[bold red]✘ Task {args_str} marked as FAILED[/bold red]"
            )
        elif cmd == "/rmtask":
            manager.delete_task(args_str)
            self._console.print(f"[dim]Task {args_str} deleted[/dim]")

    # ------------------------------------------------------------------ #
    # Startup banner w/ quick health summary
    # ------------------------------------------------------------------ #

    @staticmethod
    def collect_health_summary() -> Tuple[str, str, str]:
        """Best-effort (version, provider, model) triple for the banner."""
        version = "dev"
        try:
            from importlib.metadata import version as _pkg_version

            version = _pkg_version("aja-core")
        except Exception:
            pass
        provider, model = "unknown", "unknown"
        try:
            from aja.config import AJA_PLANNER_MODEL

            provider, _, model = AJA_PLANNER_MODEL.partition(":")
            provider, model = provider or "unknown", model or AJA_PLANNER_MODEL
        except Exception:
            pass
        return (version, provider, model)

    def print_banner(self, summary: Optional[Tuple[str, str, str]] = None) -> None:
        version, provider, model = summary or self.collect_health_summary()
        body = Text()
        body.append("AJA Terminal Assistant\n", style="bold cyan")
        body.append("version ", style="dim")
        body.append(version, style="green")
        body.append(" · provider ", style="dim")
        body.append(provider, style="magenta")
        body.append(" · model ", style="dim")
        body.append(model, style="magenta")
        self._console.print(
            Panel(
                body,
                subtitle="/help for commands · Ctrl+C cancel · Ctrl+D exit",
                border_style="cyan",
            )
        )

    # ------------------------------------------------------------------ #
    # Approval gate
    # ------------------------------------------------------------------ #

    async def handle_approval(self, ev: ApprovalRequested) -> None:
        """Render the ApprovalCard inline, collect y/n/a, resolve via core."""
        self.render_approval(ev)
        if self._approval_resolver is not None:
            approved = bool(await self._approval_resolver(ev))
        else:
            answer = await self._ask_approval_answer()
            approved = answer.strip().lower() in ("y", "yes", "a", "always")
        try:
            await self.core.resolve_approval(ev.approval_id, approved, approver_id=self._user_id)
        except Exception as e:  # best-effort: never kill the turn here
            self._console.print(f"[dim]approval resolution failed: {e}[/dim]")
        status = "[green]approved[/green]" if approved else "[red]rejected[/red]"
        self._console.print(f"Approval {status}. [dim](id={ev.approval_id})[/dim]")

    def render_approval(self, ev: ApprovalRequested) -> None:
        """Inline ApprovalCard via the shared EventRenderer pipeline."""
        self._console.print()
        self._console.print(self._renderer.render_approval(ev.approval_id, ev.reason))
        self._console.print("[dim]Approve? [y/n/a][/]")

    async def _ask_approval_answer(self) -> str:
        if self._input_provider is not None:
            return await self._input_provider("approve? [y/n/a] ")
        return await asyncio.to_thread(input, "approve? [y/n/a] ")

    # ------------------------------------------------------------------ #
    # Event rendering (delegates to EventRenderer)
    # ------------------------------------------------------------------ #

    def render_event(self, ev: CoreEvent) -> None:
        if isinstance(ev, Delta):
            self._feed_delta(ev.text)
        elif isinstance(ev, ToolStarted):
            self.render_tool_started(ev)
        elif isinstance(ev, ToolFinished):
            self.render_tool_finished(ev)
        elif isinstance(ev, Error):
            self.render_error(ev)
        elif isinstance(ev, Final):
            self.render_final(ev)

    def render_delta(self, text: str) -> None:
        self._feed_delta(text)

    def render_tool_started(self, ev: ToolStarted) -> None:
        self._end_stream()  # spinner replaces any active stream view
        if self._console.is_terminal:
            self._live = Live(
                Spinner("dots", text=Text(f" ⚙ {ev.name}", style="dim yellow")),
                console=self._console,
                refresh_per_second=12,
                transient=False,
            )
            self._live.start()
        else:
            self._renderer.render_tool_started(ev.name, ev.args_summary)

    def render_tool_finished(self, ev: ToolFinished) -> None:
        self._end_stream()  # replace the spinner with the result line
        self._renderer.render_tool_finished(ev.name, ev.success, ev.duration_ms)

    def render_error(self, ev: Error) -> None:
        self._end_stream()
        self._console.print(self._renderer.render_error(ev.code, ev.message, ev.recoverable))

    def render_final(self, ev: Final) -> None:
        self._end_stream()
        self._console.print()
        if not (ev.text or "").strip():
            self._console.print(
                "[yellow]⚠ The model returned an empty response. "
                "Check provider configuration with 'aja doctor'.[/yellow]"
            )
            return
        self._console.print(
            Panel(self._renderer.render_final(ev.text), title="AJA", border_style="cyan")
        )


def _resolve_production_stack():
    """Resolve gateway/tools/executor for the default core (lazy, test-free path)."""
    from aja.llm import get_gateway
    from aja.orchestration.tools.executor import ToolExecutor
    from aja.orchestration.tools.native import NativeToolRegistry

    return (get_gateway(), NativeToolRegistry(), ToolExecutor())
