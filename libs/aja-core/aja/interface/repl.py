"""TerminalREPL — best-in-class terminal chat surface for ConversationCore.

Consumes an ``InboundMessage``-producing input loop and renders the typed
``CoreEvent`` stream yielded by :meth:`ConversationCore.handle` natively:

* ``Delta``             -> inline streaming chunks (no newline)
* ``ToolStarted``       -> dim tool-call line
* ``ToolFinished``      -> green checkmark / red X with timing
* ``ApprovalRequested`` -> rich Panel + y/n/a gate
* ``Error``             -> red boxed Panel with code + message
* ``Final``             -> rich Markdown panel

Import-time purity: heavy wiring (ConversationCore construction) is lazy;
prompt_toolkit sessions are only built when no injected input provider is
supplied, keeping mocked turns fully testable under plain pytest.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
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
from aja.messaging.envelope import InboundMessage

__all__ = ["TerminalREPL"]

InputProvider = Callable[[str], Awaitable[str]]
ApprovalResolver = Callable[[ApprovalRequested], Awaitable[bool]]

_HELP_TEXT = """\
**Commands**
- `/help`   — show this help
- `/clear`  — clear the terminal screen
- `/exit`   — quit AJA (or press Ctrl+D)

**Keys**
- `Alt+Enter` — newline inside multi-line input
- `Enter`     — send message
- `Ctrl+C`    — cancel the current turn
- `Ctrl+D`    — exit
"""


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
        self._chat_id = chat_id
        self._user_id = user_id
        self._input_provider = input_provider
        self._approval_resolver = approval_resolver
        self._banner = banner
        self._session: Any = None  # prompt_toolkit.PromptSession, built lazily

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

    @staticmethod
    def _build_default_core() -> Any:
        """Build the production ConversationCore (heavy imports stay lazy)."""
        from aja.core.conversation import ConversationCore

        gateway, tools_registry, executor = _resolve_production_stack()
        return ConversationCore(
            gateway=gateway, tools_registry=tools_registry, executor=executor
        )

    def _get_session(self) -> Any:
        if self._session is None:
            try:
                from prompt_toolkit import PromptSession
                from prompt_toolkit.key_binding import KeyBindings
            except ImportError:  # pragma: no cover - prompt_toolkit is a dep
                raise RuntimeError("prompt_toolkit is required for interactive input")

            kb = KeyBindings()

            @kb.add("c-c")
            def _(event):  # type: ignore[no-untyped-def]
                event.app.exit(exception=KeyboardInterrupt())

            @kb.add("c-d")
            def _(event):  # type: ignore[no-untyped-def]
                event.app.exit(exception=EOFError())

            self._session = PromptSession(
                multiline=True,
                key_bindings=kb,
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
            self._print_banner()
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
            command = self._handle_slash_command(text)
            if command == "exit":
                break
            if command == "handled":
                continue
            await self.run_turn(text)
        self._console.print("[dim]Goodbye.[/dim]")

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
            self._console.print("\n[yellow]⚠ Turn cancelled.[/yellow]")
            raise
        except KeyboardInterrupt:
            turn_task.cancel()
            try:
                await turn_task
            except BaseException:  # noqa: BLE001 - swallow the interrupted turn
                pass
            self._console.print("\n[yellow]⚠ Turn cancelled.[/yellow]")
        except Exception as e:
            self.render_error(Error(code="REPL_FAILED", message=f"{type(e).__name__}: {e}"))

    async def _consume_events(self, msg: InboundMessage) -> None:
        async for ev in self.core.handle(msg):
            if isinstance(ev, ApprovalRequested):
                await self.handle_approval(ev)
            else:
                self.render_event(ev)

    # ------------------------------------------------------------------ #
    # Slash commands
    # ------------------------------------------------------------------ #

    def _handle_slash_command(self, text: str) -> str:
        low = text.lower().strip()
        if not low.startswith("/"):
            return ""
        if low in ("/exit", "/quit"):
            return "exit"
        if low == "/help":
            self._console.print(Panel(Markdown(_HELP_TEXT), title="AJA Help", border_style="cyan"))
            return "handled"
        if low == "/clear":
            self._console.clear()
            return "handled"
        self._console.print(f"[dim]Unknown command: {text.split()[0]}[/dim]")
        return "handled"

    def _print_banner(self) -> None:
        self._console.print(
            Panel(
                Text("AJA Terminal Assistant", style="bold cyan"),
                subtitle="/help for commands · Ctrl+C cancel · Ctrl+D exit",
                border_style="cyan",
            )
        )

    # ------------------------------------------------------------------ #
    # Approval gate
    # ------------------------------------------------------------------ #

    async def handle_approval(self, ev: ApprovalRequested) -> None:
        """Render the gate, collect y/n/a, resolve through the core."""
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
        self._console.print()
        self._console.print(
            Panel(
                f"[bold]{ev.reason}[/bold]\n\n[dim]approval_id: {ev.approval_id}[/dim]",
                title="🔒 Approval Required",
                border_style="yellow",
            )
        )

    async def _ask_approval_answer(self) -> str:
        if self._input_provider is not None:
            return await self._input_provider("approve? [y/n/a] ")
        return await asyncio.to_thread(input, "approve? [y/n/a] ")

    # ------------------------------------------------------------------ #
    # Event rendering
    # ------------------------------------------------------------------ #

    def render_event(self, ev: CoreEvent) -> None:
        if isinstance(ev, Delta):
            self.render_delta(ev.text)
        elif isinstance(ev, ToolStarted):
            self.render_tool_started(ev)
        elif isinstance(ev, ToolFinished):
            self.render_tool_finished(ev)
        elif isinstance(ev, Error):
            self.render_error(ev)
        elif isinstance(ev, Final):
            self.render_final(ev)

    def render_delta(self, text: str) -> None:
        self._console.print(text, end="", markup=False, highlight=False)

    def render_tool_started(self, ev: ToolStarted) -> None:
        summary = ev.args_summary.replace("\n", " ")[:120]
        line = f"⚙ {ev.name}({summary})" if summary else f"⚙ {ev.name}()"
        self._console.print(Text(line, style="dim"))

    def render_tool_finished(self, ev: ToolFinished) -> None:
        ms = f"{ev.duration_ms:.0f}ms" if ev.duration_ms else ""
        if ev.success:
            mark = Text(f"✔ {ev.name}", style="green")
        else:
            mark = Text(f"✘ {ev.name}", style="red")
        suffix = f" ({ms})" if ms else ""
        self._console.print(mark + Text(suffix, style="dim"))

    def render_error(self, ev: Error) -> None:
        body = Text.assemble(
            ("Code: ", "bold"),
            (ev.code, "bold red"),
            ("\n\n", ""),
            ("Message: ", "bold"),
            (ev.message, ""),
            ("\n", ""),
            ("Recoverable: ", "bold"),
            ("yes" if ev.recoverable else "no", "yellow" if ev.recoverable else "red"),
        )
        self._console.print(Panel(body, title="❌ Error", border_style="red"))

    def render_final(self, ev: Final) -> None:
        self._console.print()
        self._console.print(
            Panel(Markdown(ev.text or "*no content*"), title="AJA", border_style="cyan")
        )


def _resolve_production_stack():
    """Resolve gateway/tools/executor for the default core (lazy, test-free path)."""
    from aja.llm import get_gateway
    from aja.orchestration.tools.executor import ToolExecutor
    from aja.orchestration.tools.native import NativeToolRegistry

    return (get_gateway(), NativeToolRegistry(), ToolExecutor())
