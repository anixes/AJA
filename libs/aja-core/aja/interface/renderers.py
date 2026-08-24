"""Rich renderers for ConversationCore typed events.

Single source of truth consumed by TerminalREPL and TextualDashboard:
each ``CoreEvent`` from ``ConversationCore.handle()`` maps to a Rich
renderable or inline console write here, so every surface renders
identically.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
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
from aja.utils.redact import redact_secrets

__all__ = ["EventRenderer"]


class EventRenderer:
    """Renders ConversationCore events as Rich renderables.

    Single source of truth consumed by TerminalREPL and TextualDashboard.
    """

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self._delta_buffer: list[str] = []

    # ------------------------------------------------------------------ #
    # Individual event renderers
    # ------------------------------------------------------------------ #

    def render_delta(self, text: str) -> None:
        """Prints streaming text chunk inline without newline."""
        self._delta_buffer.append(text)
        safe = escape(text)
        self.console.print(safe, end="", soft_wrap=True)

    @property
    def delta_buffer(self) -> str:
        """Accumulated streaming text so far this turn."""
        return "".join(self._delta_buffer)

    def reset_stream(self) -> None:
        """Clear the accumulated Delta buffer (call at turn start)."""
        self._delta_buffer.clear()

    def render_tool_started(self, name: str, args_summary: str) -> None:
        """Dim yellow line showing tool call beginning."""
        summary = redact_secrets(args_summary)
        line = Text()
        line.append(f"⚙ {name}", style="dim yellow")
        if summary:
            line.append(f"({escape(summary)})", style="dim yellow")
        self.console.print(line)

    def render_tool_finished(self, name: str, success: bool, duration_ms: float) -> None:
        """Replaces spinner with ✓ green or ✗ red result."""
        mark = "✓" if success else "✗"
        style = "bold green" if success else "bold red"
        line = Text()
        line.append(f"{mark} {name}", style=style)
        line.append(f" ({duration_ms:.0f}ms)", style="dim")
        self.console.print(line)

    def render_approval(
        self, approval_id: str, reason: str, command: str = ""
    ) -> Panel:
        """Returns an inline approval card panel."""
        body = Text()
        body.append("Reason: ", style="bold")
        body.append(escape(reason))
        if command:
            body.append("\nCommand: ", style="bold")
            body.append(escape(redact_secrets(command)), style="yellow")
        return Panel(
            body,
            title=f"[bold yellow]Approval Required[/] [dim]{escape(approval_id)}[/]",
            border_style="yellow",
            expand=False,
        )

    def render_error(self, code: str, message: str, recoverable: bool = True) -> Panel:
        """Red boxed error panel with severity color coding."""
        severity = "RECOVERABLE" if recoverable else "FATAL"
        border = "red" if recoverable else "bright_red bold"
        title_style = "red" if recoverable else "bold white on red"
        title = Text.assemble(
            ("Error", title_style),
            (f" · {code} ", "dim"),
            (f"[{severity}]", "yellow" if recoverable else "bold red"),
        )
        return Panel(Text(escape(message)), title=title, border_style=border)

    def await_approval(
        self, approval_id: str, reason: str, command: str = ""
    ) -> bool:
        """Display the approval card and block on operator input.

        Returns True when the operator approves (y/yes), False otherwise.
        """
        self.console.print(self.render_approval(approval_id, reason, command))
        try:
            answer = input("Approve? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    def render_final(self, text: str) -> Markdown:
        """Full markdown panel for the final response."""
        return Markdown(redact_secrets(text))

    # ------------------------------------------------------------------ #
    # Stream driver
    # ------------------------------------------------------------------ #

    async def stream_events(self, core_handle_result: Any) -> Optional[str]:
        """Iterate ``ConversationCore.handle()`` events, rendering each.

        Accepts either an async iterable of :data:`CoreEvent` or an
        awaitable resolving to one. Renders each event via the matching
        method and returns the text of the terminal :class:`Final`
        event, or ``None`` if the stream ended without one.
        """
        if hasattr(core_handle_result, "__aiter__"):
            events: AsyncIterator[CoreEvent] = core_handle_result
        else:
            events = await core_handle_result

        final_text: Optional[str] = None
        self.reset_stream()
        async for event in events:
            if isinstance(event, Delta):
                self.render_delta(event.text)
            elif isinstance(event, ToolStarted):
                self.render_tool_started(event.name, event.args_summary)
            elif isinstance(event, ToolFinished):
                self.render_tool_finished(event.name, event.success, event.duration_ms)
            elif isinstance(event, ApprovalRequested):
                self.console.print(
                    self.render_approval(event.approval_id, event.reason)
                )
            elif isinstance(event, Error):
                self.console.print(
                    self.render_error(event.code, event.message, event.recoverable)
                )
            elif isinstance(event, Final):
                final_text = event.text
        return final_text
