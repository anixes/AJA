"""AJADashboard v2 — mockup-matched Textual dashboard.

Layout (120x36 target)::

    ┌ AJA ● health ─────────────────────────────── hybrid · gpt-4o ──┐
    │╭─ CHAT ────────────────────────────────────╮╭─ FOCUS ──────────╮│
    ││ you › what's on my plate?                 ││ 1 ▲94 overdue …  ││
    ││ ⠸ thinking… / ✓ tool 412ms                │╰─ MISSIONS ───────╯│
    ││ ╭ AJA markdown ╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱   ││ m_a1f2 research…  ││
    │╰───────────────────────────────────────────╯╰───────────────────╯│
    ├─ INPUT ─ Enter send · ctrl+c interrupt · / cmds ─────────────────┤
    └ [F1]help [F2]missions [F3]briefing [s]skins [ctrl+q]quit ────────┘

Left pane streams ``ConversationCore`` events; right pane is a
``TabbedContent`` sidebar with FOCUS | MISSIONS | DAY tabs refreshed from the
priority engine, mission store, and scheduler respectively.

Import-time purity: heavy AJA subsystems resolve lazily inside default
providers. Tests inject mocks via the constructor.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, Union

from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

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

__all__ = ["AJADashboard", "ApprovalCard"]

SIDEBAR_REFRESH_S = 30.0
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SKINS: Sequence[str] = ("default", "cyberpunk", "ares")

HealthCheckFn = Callable[[], Union[List[Dict[str, Any]], "asyncio.Future", Any]]
ListProviderFn = Callable[[], Union[List[Dict[str, Any]], Any]]
BriefingFn = Callable[[], str]
ApprovalResolverFn = Callable[[str, bool], Any]


# --------------------------------------------------------------------- #
# Default (lazy) providers — never imported at module scope
# --------------------------------------------------------------------- #


def _default_health_check() -> List[Dict[str, Any]]:
    """`aja doctor --quick` equivalent: cheap, in-memory liveness checks."""
    import sys

    checks: List[Dict[str, Any]] = [
        {"name": "python", "ok": sys.version_info >= (3, 11), "detail": sys.version.split()[0]}
    ]
    try:
        from aja import tui  # noqa: F401

        checks.append({"name": "core", "ok": True, "detail": "importable"})
    except Exception as e:
        checks.append({"name": "core", "ok": False, "detail": str(e)[:60]})
    try:
        import lancedb  # noqa: F401

        checks.append({"name": "lancedb", "ok": True, "detail": "available"})
    except Exception:
        checks.append({"name": "lancedb", "ok": False, "detail": "missing (optional)"})
    return checks


def _default_focus_refresh() -> List[Dict[str, Any]]:
    """Top-3 priority-engine tasks for the FOCUS tab."""
    from aja.api.services.priority_engine import run_priority_engine
    from aja.memory.secretary import AJAMemory

    return list(run_priority_engine(AJAMemory()).get("top3", []))


def _default_missions_refresh() -> List[Dict[str, Any]]:
    """ACTIVE + PENDING missions for the MISSIONS tab."""
    from aja.memory.secretary import AJAMemory

    memory = AJAMemory()
    active = memory.list_missions(status="ACTIVE") or []
    pending = memory.list_missions(status="PENDING") or []
    return [*active, *pending]


def _default_day_refresh() -> List[Dict[str, Any]]:
    """Today's one-shot reminders for the DAY tab."""
    from aja.scheduler.cron_scheduler import CronScheduler

    reminders: List[Dict[str, Any]] = []
    for job in CronScheduler().list_jobs():
        expr = str(job.get("schedule_expr") or "")
        is_reminder = expr.startswith("at:") or "remind" in str(job.get("goal", "")).lower()
        if not is_reminder:
            continue
        reminders.append(
            {
                "goal": job.get("goal", ""),
                "schedule_expr": job.get("schedule_expr"),
                "paused": bool(job.get("paused")),
            }
        )
    return reminders


def _default_briefing() -> str:
    from aja.assistant.briefing import compose_briefing

    return compose_briefing()


class HelpScreen(ModalScreen):
    """F1 overlay — keyboard shortcut reference."""

    BINDINGS = [("escape", "dismiss_overlay", "Close"), ("f1", "dismiss_overlay", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(
            Text.from_markup(
                "[bold cyan]AJA Dashboard — Shortcuts[/]\n\n"
                "  [b]Enter[/]      send message\n"
                "  [b]ctrl+c[/]    interrupt current turn\n"
                "  [b]tab[/]       cycle focus (input → sidebar → chat)\n"
                "  [b]f1[/]        this help overlay\n"
                "  [b]f2[/]        sidebar → MISSIONS view\n"
                "  [b]f3[/]        daily briefing screen\n"
                "  [b]s[/]         cycle theme skin (default/cyberpunk/ares)\n"
                "  [b]ctrl+q[/]    quit\n"
            ),
            id="help-box",
        )

    def action_dismiss_overlay(self) -> None:
        self.dismiss()


class BriefingScreen(ModalScreen):
    """F3 modal — composed daily briefing markdown."""

    BINDINGS = [("escape", "dismiss_overlay", "Close"), ("f3", "dismiss_overlay", "Close")]

    def __init__(self, markdown_text: str) -> None:
        super().__init__()
        self._markdown_text = markdown_text or "_Briefing unavailable._"

    def compose(self) -> ComposeResult:
        from textual.widgets import Markdown

        yield VerticalScroll(Markdown(self._markdown_text), id="briefing-scroll")
        yield Static("[dim]esc / f3 to close[/]", id="briefing-hint")

    def action_dismiss_overlay(self) -> None:
        self.dismiss()


class ApprovalCard(Vertical):
    """Inline interactive approval gate rendered into the chat flow."""

    def __init__(self, approval_id: str, reason: str) -> None:
        super().__init__()
        self.approval_id = approval_id
        self.reason = reason

    def compose(self) -> ComposeResult:
        yield Static(
            Text.from_markup(
                f"[bold yellow]⏸ approval requested[/] [dim]{escape(self.approval_id)}[/]\n"
                f"{escape(self.reason)}"
            ),
            classes="approval-text",
        )
        with Horizontal(classes="approval-buttons"):
            yield Button("Approve", variant="success", id="approve")
            yield Button("Reject", variant="error", id="reject")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        approved = event.button.id == "approve"
        for row in self.query(".approval-buttons"):
            row.display = False
        self.query_one(".approval-text", Static).update(
            Text.from_markup(
                f"[{'green' if approved else 'red'}]{'✓ approved' if approved else '✗ rejected'}[/]"
                f" [dim]{escape(self.approval_id)}[/]"
            )
        )
        app = self.app
        if isinstance(app, AJADashboard):
            await app.resolve_approval(self.approval_id, approved)


class AJADashboard(App):
    """Full-screen AJA chat + focus/mission/day monitoring dashboard."""

    TITLE = "AJA"
    CSS = """
    Screen { background: $background; }
    #main { height: 1fr; }
    #chat-pane { width: 1fr; }
    #chat-log {
        height: 1fr;
        border: round $accent;
        background: $surface;
        padding: 0 1;
    }
    #status-line { height: 1; padding: 0 1; display: none; }
    #approval-dock { height: auto; display: none; }
    ApprovalCard {
        height: auto;
        border: round yellow;
        background: $surface;
        padding: 0 1;
        margin-bottom: 1;
    }
    .approval-buttons { height: auto; }
    .approval-buttons Button { margin-right: 1; }
    #sidebar { width: 34; min-width: 34; }
    #sidebar-tabs { height: 1fr; }
    #sidebar-tabs ContentArea { padding: 0 1; }
    TabPane { border: round $accent; background: $surface; padding: 0 1; }
    #command-input { dock: bottom; border: round $accent; }
    #help-box, #briefing-scroll {
        width: 60%;
        margin-top: 4;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #briefing-hint { align: center bottom; }

    /* Skin: cyberpunk */
    .-skin-cyberpunk Screen { background: #0a0a14; }
    .-skin-cyberpunk #chat-log, .-skin-cyberpunk TabPane,
    .-skin-cyberpunk #command-input { border: round magenta; }
    .-skin-cyberpunk Header { background: magenta; }

    /* Skin: ares */
    .-skin-ares Screen { background: #140a08; }
    .-skin-ares #chat-log, .-skin-ares TabPane,
    .-skin-ares #command-input { border: round red; }
    .-skin-ares Header { background: red; }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+c", "interrupt_turn", "Interrupt"),
        ("tab", "cycle_focus", "Cycle focus"),
        ("f1", "help_overlay", "Help"),
        ("f2", "sidebar_missions", "Missions"),
        ("f3", "briefing_screen", "Briefing"),
        ("s", "cycle_skin", "Skin"),
    ]

    def __init__(
        self,
        *,
        core: Optional[Any] = None,
        core_factory: Optional[Callable[[], Any]] = None,
        health_check: Optional[HealthCheckFn] = None,
        sidebar_refresh: Optional[Callable[[], Dict[str, Any]]] = None,
        focus_refresh: Optional[ListProviderFn] = None,
        missions_refresh: Optional[ListProviderFn] = None,
        day_refresh: Optional[ListProviderFn] = None,
        briefing_fn: Optional[BriefingFn] = None,
        approval_resolver: Optional[ApprovalResolverFn] = None,
        accent_color: str = "cyan",
        surface: str = "tui",
        user_id: str = "operator",
        chat_id: str = "tui-dashboard",
        model_info: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.core = core
        self._core_factory = core_factory
        self._health_check = health_check or _default_health_check
        self._legacy_sidebar_refresh = sidebar_refresh
        self._focus_refresh = focus_refresh or _default_focus_refresh
        self._missions_refresh = missions_refresh or (
            lambda: ((sidebar_refresh() or {}).get("missions", [])) if sidebar_refresh else []
        )
        self._day_refresh = day_refresh or _default_day_refresh
        self._briefing_fn = briefing_fn or _default_briefing
        self._approval_resolver = approval_resolver
        self.accent_color = accent_color
        self._surface = surface
        self._user_id = user_id
        self._chat_id = chat_id
        self.model_info = model_info or "auto"
        self._turn_task: Optional[asyncio.Task] = None
        self.turn_count = 0
        self.skin_index = 0
        self._delta_buffer: List[str] = []
        self._spinner_i = 0
        self._resolved_approvals: Dict[str, bool] = {}

    # ------------------------------------------------------------------ #
    # Composition
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="chat-pane"):
                yield RichLog(id="chat-log", highlight=True, markup=False, wrap=True)
                yield Static("", id="status-line")
                yield Vertical(id="approval-dock")
            with Vertical(id="sidebar"):
                with TabbedContent(id="sidebar-tabs", initial="focus-pane"):
                    with TabPane("FOCUS", id="focus-pane"):
                        yield Static("Loading…", id="focus-list")
                    with TabPane("MISSIONS", id="missions-pane"):
                        yield Static("Loading…", id="missions-list")
                    with TabPane("DAY", id="day-pane"):
                        yield Static("Loading…", id="day-list")
        yield Input(placeholder="Talk to AJA…", id="command-input")
        yield Footer()

    async def on_mount(self) -> None:
        health, model = await self._run_quick_checks()
        self.sub_title = f"{health} · {model}"
        self.title = f"AJA ● {'healthy' if '!' not in health else 'degraded'}"
        self.set_interval(SIDEBAR_REFRESH_S, self.refresh_sidebar)
        self.set_interval(0.12, self._tick_spinner)
        await self.refresh_sidebar()
        self.query_one("#chat-log", RichLog).write(
            Text.from_markup("[bold cyan]AJA[/] ready — type below.")
        )
        self.query_one("#command-input").focus()

    # ------------------------------------------------------------------ #
    # Health → header
    # ------------------------------------------------------------------ #

    async def _run_quick_checks(self) -> tuple[str, str]:
        try:
            result = self._health_check()
            if asyncio.iscoroutine(result):
                result = await result
            checks = list(result or [])
        except Exception as e:  # best-effort: header must always render
            checks = [{"name": "quickcheck", "ok": False, "detail": f"{type(e).__name__}: {e}"}]
        passed = sum(1 for c in checks if c.get("ok"))
        glyph = "✓" if passed == len(checks) else "!"
        detail = ", ".join(f"{c['name']}:{'ok' if c.get('ok') else 'FAIL'}" for c in checks[:6])
        return f"{glyph} {passed}/{len(checks)} checks · {detail}", str(self.model_info)

    # ------------------------------------------------------------------ #
    # Sidebar refresh
    # ------------------------------------------------------------------ #

    async def refresh_sidebar(self) -> None:
        results = await asyncio.gather(
            self._safe_provider(self._focus_refresh),
            self._safe_provider(self._missions_refresh),
            self._safe_provider(self._day_refresh),
        )
        focus_items, missions, reminders = results
        self._update_focus_tab(focus_items)
        self._update_missions_tab(missions)
        self._update_day_tab(reminders)

    async def _safe_provider(self, fn: Callable[[], Any]) -> List[Dict[str, Any]]:
        try:
            data = fn()
            if asyncio.iscoroutine(data):
                data = await data
            return list(data or [])
        except Exception as e:
            return [{"_error": f"{type(e).__name__}: {e}"}]

    def _update_focus_tab(self, items: List[Dict[str, Any]]) -> None:
        lines: List[str] = []
        n = 0
        for item in items:
            if "_error" in item:
                lines.append(f"[red]⚠ {escape(item['_error'])}[/]")
                break
            n += 1
            score = int(item.get("priority_score") or 0)
            title = str(item.get("title") or item.get("task") or item.get("goal") or "?")[:18]
            due = str(item.get("due_label") or item.get("due_date") or "")
            tier = str(item.get("urgency_tier") or "")
            color = {"critical": "red", "high": "yellow"}.get(tier, "green")
            arrow = "▲" if score >= 60 else "•"
            lines.append(f"{n} [bold {color}]{arrow}{score}[/] {escape(due)} {escape(title)}")
        if not lines:
            lines.append("[dim](nothing on your plate)[/]")
        self.query_one("#focus-list", Static).update(Text.from_markup("\n".join(lines)))

    def _update_missions_tab(self, missions: List[Dict[str, Any]]) -> None:
        lines: List[str] = []
        for m in missions[:6]:
            if "_error" in m:
                lines.append(f"[red]⚠ {escape(m['_error'])}[/]")
                break
            mid = str(m.get("mission_id") or m.get("id") or "?")[:10]
            goal = str(m.get("goal") or "?")[:20]
            status = str(m.get("status") or "?").lower()
            style = "green" if status == "active" else "dim"
            lines.append(f"[{style}]●[/] [dim]{escape(mid)}[/] {escape(goal)}")
        if not lines:
            lines.append("[dim](no active missions)[/]")
        self.query_one("#missions-list", Static).update(Text.from_markup("\n".join(lines)))

    def _update_day_tab(self, reminders: List[Dict[str, Any]]) -> None:
        lines: List[str] = ["[bold]Reminders today[/]"]
        shown = 0
        for r in reminders[:8]:
            if "_error" in r:
                lines.append(f"[red]⚠ {escape(r['_error'])}[/]")
                break
            when = str(r.get("schedule_expr") or "").removeprefix("at:")
            flag = "[dim]paused[/]" if r.get("paused") else "⏰"
            lines.append(f"{flag} {escape(when)} — {escape(str(r.get('goal', ''))[:24])}")
            shown += 1
        if not shown and len(lines) < 2:
            lines.append("[dim](no reminders)[/]")
        self.query_one("#day-list", Static).update(Text.from_markup("\n".join(lines)))

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
        if text.startswith("/"):
            event.input.clear()
            await self._handle_slash_command(text)
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
        self._delta_buffer.clear()
        self._set_status("thinking…")
        self._turn_task = asyncio.create_task(self._run_turn(msg))

    async def _handle_slash_command(self, text: str) -> None:
        cmd = text.split()[0].lower()
        if cmd in ("/help", "/?"):
            await self.run_action("help_overlay")
        elif cmd == "/missions":
            self.run_action("sidebar_missions")
        elif cmd == "/briefing":
            await self.run_action("briefing_screen")
        elif cmd == "/skin":
            self.run_action("cycle_skin")
        else:
            self._append_system_line(f"unknown command {cmd}", style="yellow")

    async def _run_turn(self, msg: InboundMessage) -> None:
        try:
            core = self._ensure_core()
        except Exception as e:
            self._render_error_panel("CORE_UNAVAILABLE", f"{type(e).__name__}: {e}")
            self._clear_status()
            return
        try:
            async for ev in core.handle(msg):
                await self.render_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._render_error_panel("TURN_FAILED", f"{type(e).__name__}: {e}")
        finally:
            self._clear_status()

    async def render_event(self, ev: CoreEvent) -> None:
        """Render one typed CoreEvent into the chat flow."""
        log = self.query_one("#chat-log", RichLog)
        if isinstance(ev, Delta):
            self._delta_buffer.append(ev.text)
            preview = "".join(self._delta_buffer)[-80:].replace("\n", " ")
            self._set_status(preview)
        elif isinstance(ev, ToolStarted):
            self._set_status(f"running {ev.name}…")
            log.write(Text.from_markup(f"  [dim]⚙ {escape(ev.name)}({escape(ev.args_summary or '')})…[/]"))
        elif isinstance(ev, ToolFinished):
            mark = "✓" if ev.success else "✗"
            style = "green" if ev.success else "red"
            self._set_status("")
            log.write(Text.from_markup(f"  [{style}]{mark} {ev.name} ({ev.duration_ms:.0f}ms)[/]"))
        elif isinstance(ev, Error):
            self._render_error_panel(ev.code, ev.message)
        elif isinstance(ev, ApprovalRequested):
            dock = self.query_one("#approval-dock", Vertical)
            dock.display = True
            await dock.mount(ApprovalCard(ev.approval_id, ev.reason))
        elif isinstance(ev, Final):
            self._delta_buffer.clear()
            if ev.text:
                log.write(Panel(Markdown(ev.text), border_style=self.accent_color, title="AJA"))

    async def resolve_approval(self, approval_id: str, approved: bool) -> None:
        """Forward an inline card decision to the injected resolver."""
        self._resolved_approvals[approval_id] = approved
        if self._approval_resolver is None:
            self._append_system_line(
                f"approval {approval_id} recorded locally ({'approved' if approved else 'rejected'})"
            )
            return
        result = self._approval_resolver(approval_id, approved)
        if asyncio.iscoroutine(result):
            await result

    # Status line / spinner -------------------------------------------------

    def _set_status(self, detail: str) -> None:
        status = self.query_one("#status-line", Static)
        frame = SPINNER_FRAMES[self._spinner_i % len(SPINNER_FRAMES)]
        status.update(Text.from_markup(f"[cyan]{frame}[/] {escape(detail)}"))
        status.display = True

    def _clear_status(self) -> None:
        status = self.query_one("#status-line", Static)
        status.update("")
        status.display = False

    def _tick_spinner(self) -> None:
        self._spinner_i += 1
        status = self.query_one("#status-line", Static)
        if not status.display:
            return
        detail = "".join(self._delta_buffer)[-80:] if self._delta_buffer else "thinking…"
        self._set_status(detail.replace("\n", " "))

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
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # best-effort: interruption must not raise
            pass
        self._clear_status()
        self._append_system_line("■ turn interrupted", style="yellow")

    def action_cycle_focus(self) -> None:
        order = ["#command-input", "#sidebar-tabs", "#chat-log"]
        focused = self.focused
        current = -1

        def matches(widget: Any) -> bool:
            node = focused
            while node is not None:
                if node is widget:
                    return True
                node = getattr(node, "_parent", None) or getattr(node, "parent", None)
            return False

        for i, sel in enumerate(order):
            if matches(self.query_one(sel)):
                current = i
                break
        nxt = self.query_one(order[(current + 1) % len(order)])
        if nxt.can_focus:
            nxt.focus()
        else:
            self.set_focus(None)

    def action_sidebar_missions(self) -> None:
        tabs = self.query_one("#sidebar-tabs", TabbedContent)
        tabs.active = "missions-pane"

    async def action_help_overlay(self) -> None:
        await self.push_screen(HelpScreen())

    async def action_briefing_screen(self) -> None:
        try:
            text = self._briefing_fn()
            if asyncio.iscoroutine(text):
                text = await text
        except Exception as e:  # best-effort: briefing must never crash the app
            text = f"_Briefing failed: {type(e).__name__}: {escape(str(e))}_"
        await self.push_screen(BriefingScreen(text))

    def action_cycle_skin(self) -> None:
        self.skin_index = (self.skin_index + 1) % len(SKINS)
        skin = SKINS[self.skin_index]
        for old in SKINS:
            self.remove_class(f"-skin-{old}")
        self.add_class(f"-skin-{skin}")
        self._append_system_line(f"skin → {skin}")
