"""AJADashboard — full-screen interactive Textual dashboard.

Replaces the read-only curses TUI with a chat-first surface: users talk to
``ConversationCore`` and monitor missions in the same screen.

Layout:
    ┌────────────── Header (logo + quick-check status) ──────────────┐
    │ RichLog (streaming chat)                        │ Sidebar      │
    │ Tool progress lines inline                      │ missions /   │
    │ Final answers rendered as markdown              │ workers      │
    ├────────────────── Input (docked bottom) ───────────────────────┤
    └────────────────── Footer (shortcut help) ──────────────────────┘

Import-time purity: heavy AJA subsystems (LanceDB, gateway) resolve lazily.
Tests inject a mock core / refresh fn / health check via the constructor.

This module is standalone-safe: it only requires ``textual`` + ``rich`` at
import time, both stdlib-independent third-party packages.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Union

from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, RichLog, Static

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

__all__ = ["AJADashboard"]

SIDEBAR_REFRESH_S = 30.0

HealthCheckFn = Callable[[], Union[List[Dict[str, Any]], "Awaitable[List[Dict[str, Any]]]"]]
SidebarRefreshFn = Callable[[], Union[Dict[str, Any], "Awaitable[Dict[str, Any]]"]]


# --------------------------------------------------------------------- #
# Default (lazy) providers — never imported at module scope
# --------------------------------------------------------------------- #


def _default_health_check() -> List[Dict[str, Any]]:
    """`aja doctor --quick` equivalent: cheap, in-memory liveness checks."""
    import sys

    checks: List[Dict[str, Any]] = []
    checks.append({"name": "python", "ok": sys.version_info >= (3, 11), "detail": sys.version.split()[0]})
    try:
        from aja import tui  # noqa: F401  package integrity

        checks.append({"name": "core", "ok": True, "detail": "importable"})
    except Exception as e:
        checks.append({"name": "core", "ok": False, "detail": str(e)[:60]})
    try:
        import lancedb  # noqa: F401

        checks.append({"name": "lancedb", "ok": True, "detail": "available"})
    except Exception:
        checks.append({"name": "lancedb", "ok": False, "detail": "missing (optional)"})
    return checks


def _default_sidebar_refresh() -> Dict[str, Any]:
    """Best-effort LanceDB snapshot: active missions + worker health."""
    from aja.runtime.lance_stores import LanceRuntimeStore

    store = LanceRuntimeStore()
    active = store.list_missions(status="in_progress") or []
    workers = store.list_workers() or []
    healthy = [w for w in workers if str(w.get("status", "")).lower() in ("healthy", "online", "idle")]
    return {
        "active_missions": len(active),
        "missions": [
            {
                "id": str(m.get("mission_id", "?"))[:14],
                "goal": str(m.get("goal", ""))[:40],
            }
            for m in active[:5]
        ],
        "workers_total": len(workers),
        "workers_healthy": len(healthy),
    }


class AJADashboard(App):
    """Full-screen AJA chat + mission monitoring dashboard."""

    TITLE = "AJA"

    CSS = """
    Screen { background: $background; }
    #main { height: 1fr; }
    #chat-log {
        width: 1fr;
        border: round $accent;
        background: $surface;
        padding: 0 1;
    }
    #sidebar {
        dock: right;
        width: 34;
        border: round $accent;
        background: $surface;
        padding: 0 1;
    }
    #command-input {
        dock: bottom;
        border: round $accent;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+c", "interrupt_turn", "Interrupt turn"),
        ("tab", "focus_next", "Next focus"),
    ]

    def __init__(
        self,
        *,
        core: Optional[Any] = None,
        core_factory: Optional[Callable[[], Any]] = None,
        health_check: Optional[HealthCheckFn] = None,
        sidebar_refresh: Optional[SidebarRefreshFn] = None,
        accent_color: str = "cyan",
        surface: str = "tui",
        user_id: str = "operator",
        chat_id: str = "tui-dashboard",
    ) -> None:
        super().__init__()
        self.core = core
        self._core_factory = core_factory
        self._health_check = health_check or _default_health_check
        self._sidebar_refresh = sidebar_refresh or _default_sidebar_refresh
        self.accent_color = accent_color
        self._surface = surface
        self._user_id = user_id
        self._chat_id = chat_id
        self._turn_task: Optional[asyncio.Task] = None
        self.turn_count = 0

    # ------------------------------------------------------------------ #
    # Composition
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            yield RichLog(id="chat-log", highlight=True, markup=False, wrap=True)
            yield Static("Loading…", id="sidebar")
        yield Input(placeholder="Talk to AJA… (Enter to send)", id="command-input")
        yield Footer()

    async def on_mount(self) -> None:
        self.sub_title = "starting…"
        self.set_interval(SIDEBAR_REFRESH_S, self._refresh_sidebar_tick)
        await self._run_quick_checks()
        await self._refresh_sidebar_tick()
        self.query_one("#chat-log", RichLog).write(
            Text.from_markup("[bold cyan]AJA[/] ready — type below.")
        )

    # ------------------------------------------------------------------ #
    # Quick checks → header subtitle
    # ------------------------------------------------------------------ #

    async def _run_quick_checks(self) -> None:
        try:
            result = self._health_check()
            if asyncio.iscoroutine(result):
                result = await result
            checks = list(result or [])
        except Exception as e:  # best-effort: header must always render
            checks = [{"name": "quickcheck", "ok": False, "detail": f"{type(e).__name__}: {e}"}]
        passed = sum(1 for c in checks if c.get("ok"))
        glyph = "✓" if passed == len(checks) else "!"
        style = "green" if passed == len(checks) else "yellow"
        detail = ", ".join(f"{c['name']}:{'ok' if c.get('ok') else 'FAIL'}" for c in checks[:6])
        self.sub_title = f"[{style}]{glyph} {passed}/{len(checks)} checks[/] · {detail}"

    # ------------------------------------------------------------------ #
    # Sidebar refresh (every SIDEBAR_REFRESH_S seconds)
    # ------------------------------------------------------------------ #

    async def _refresh_sidebar_tick(self) -> None:
        try:
            data = self._sidebar_refresh()
            if asyncio.iscoroutine(data):
                data = await data
        except Exception as e:
            text = Text(f"Sidebar unavailable\n{type(e).__name__}: {e}", style="red")
            self.query_one("#sidebar", Static).update(text)
            return
        lines = [
            f"Active missions : {data.get('active_missions', 0)}",
            f"Workers         : {data.get('workers_healthy', 0)}/{data.get('workers_total', 0)} healthy",
            "",
            "[bold]Missions[/]",
        ]
        for m in data.get("missions", []):
            lines.append(f"  • {escape(str(m.get('id', '')))} {escape(str(m.get('goal', '')))}")
        if not data.get("missions"):
            lines.append("  (none)")
        self.query_one("#sidebar", Static).update(Text.from_markup("\n".join(lines)))

    # ------------------------------------------------------------------ #
    # Chat pipeline
    # ------------------------------------------------------------------ #

    def _ensure_core(self) -> Any:
        if self.core is not None:
            return self.core
        if self._core_factory is not None:
            self.core = self._core_factory()
        else:
            from aja.core.conversation import ConversationCore
            from aja.llm import get_gateway
            from aja.orchestration.tools.executor import ToolExecutor
            from aja.orchestration.tools.native import NativeToolRegistry

            self.core = ConversationCore(
                gateway=get_gateway(),
                tools_registry=NativeToolRegistry(),
                executor=ToolExecutor(),
            )
        return self.core

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self._turn_task is not None and not self._turn_task.done():
            self._append_system_line("busy — press ctrl+c to interrupt the current turn", style="yellow")
            return
        event.input.clear()
        msg = InboundMessage(
            surface=self._surface,
            chat_id=self._chat_id,
            user_id=self._user_id,
            text=text,
        )
        self.turn_count += 1
        log = self.query_one("#chat-log", RichLog)
        log.write(Text.from_markup(f"\n[bold cyan]you ›[/] {escape(text)}"))
        self._turn_task = asyncio.create_task(self._run_turn(msg))

    async def _run_turn(self, msg: InboundMessage) -> None:
        try:
            core = self._ensure_core()
        except Exception as e:
            self._render_error_panel("CORE_UNAVAILABLE", f"{type(e).__name__}: {e}")
            return
        try:
            async for ev in core.handle(msg):
                await self.render_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._render_error_panel("TURN_FAILED", f"{type(e).__name__}: {e}")

    async def render_event(self, ev: CoreEvent) -> None:
        """Render one typed CoreEvent into the chat log."""
        log = self.query_one("#chat-log", RichLog)
        if isinstance(ev, Delta):
            log.write(ev.text)
        elif isinstance(ev, ToolStarted):
            log.write(Text.from_markup(f"  [dim]⚙ {escape(ev.name)}({escape(ev.args_summary or '')})…[/]"))
        elif isinstance(ev, ToolFinished):
            mark = "✓" if ev.success else "✗"
            style = "green" if ev.success else "red"
            log.write(Text.from_markup(f"  [{style}]{mark} {ev.name} ({ev.duration_ms:.0f}ms)[/]"))
        elif isinstance(ev, Error):
            self._render_error_panel(ev.code, ev.message)
        elif isinstance(ev, ApprovalRequested):
            log.write(Text.from_markup(f"  [yellow]⏸ approval requested ({escape(ev.approval_id)}): {escape(ev.reason)}[/]"))
        elif isinstance(ev, Final):
            if ev.text:
                log.write(Panel(Markdown(ev.text), border_style=self.accent_color, title="AJA"))

    def _render_error_panel(self, code: str, message: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        body = Text(message or code, style="bold red")
        log.write(Panel(body, title=f"error · {code}", border_style="red"))

    def _append_system_line(self, text: str, style: str = "dim") -> None:
        self.query_one("#chat-log", RichLog).write(Text(text, style=style))

    # ------------------------------------------------------------------ #
    # Bindings
    # ------------------------------------------------------------------ #

    async def action_interrupt_turn(self) -> None:
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._append_system_line("■ turn interrupted", style="yellow")
