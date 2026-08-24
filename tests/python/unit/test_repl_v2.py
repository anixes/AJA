"""v2 tests for the EventRenderer-backed TerminalREPL.

Covers: scripted-event rendering through the shared EventRenderer pipeline,
streaming Delta buffering, tool spinner replacement, inline approval cards,
slash-command palette/autocomplete, keyboard-binding map, banner health
summary, and interrupt behavior.
"""
from __future__ import annotations

import asyncio
import io
from typing import AsyncIterator

from rich.console import Console

from aja.core.events import (
    ApprovalRequested,
    Delta,
    Error,
    Final,
    ToolFinished,
    ToolStarted,
)
from aja.interface.repl import SLASH_COMMANDS, TerminalREPL, build_key_bindings
from aja.messaging.envelope import InboundMessage


def make_console(force_terminal: bool = False) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=force_terminal,
        width=200,
        legacy_windows=False,
        color_system=None if not force_terminal else "truecolor",
    )
    return console, buf


class MockCore:
    """Scripted ConversationCore double."""

    def __init__(self, script: list | None = None) -> None:
        self.script = script or []
        self.received: list[InboundMessage] = []
        self.resolutions: list[tuple[str, bool, str]] = []

    async def handle(self, msg: InboundMessage) -> AsyncIterator:
        self.received.append(msg)
        for ev in self.script:
            yield ev

    async def resolve_approval(self, approval_id: str, approved: bool, approver_id: str = "") -> dict:
        self.resolutions.append((approval_id, approved, approver_id))
        return {"status": "ok", "approved": approved}


def feed(*lines: str):
    queue = list(lines)

    async def provider(prompt: str = "") -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return provider


# --------------------------------------------------------------------- #
# EventRenderer pipeline delegation
# --------------------------------------------------------------------- #


def test_repl_delegates_to_event_renderer():
    console, _ = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    assert isinstance(repl.renderer, object)
    from aja.interface.renderers import EventRenderer

    assert isinstance(repl.renderer, EventRenderer)


def test_scripted_turn_matches_expected_rich_format():
    console, buf = make_console()
    core = MockCore(
        script=[
            Delta(text="Hel"),
            Delta(text="lo world"),
            ToolStarted(name="search_web", args_summary='{"q": "aja"}'),
            ToolFinished(name="search_web", success=True, duration_ms=87.0),
            Final(text="**Answer**: 42"),
        ]
    )
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        await repl.run_turn("question")

    asyncio.run(scenario())
    out = buf.getvalue()
    assert "Hello world" in out          # buffered deltas printed inline
    assert "search_web" in out           # started line + result line
    assert "✔ search_web" in out         # REPL mark parity (EventRenderer override)
    assert "87ms" in out                 # duration suffix format
    assert "AJA" in out and "Answer" in out and "42" in out  # Final markdown panel


def test_streaming_deltas_buffered_via_renderer_pipeline():
    console, buf = make_console()
    core = MockCore(script=[Delta(text="a"), Delta(text="b"), Final(text="done")])
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        await repl.run_turn("go")

    asyncio.run(scenario())
    assert "ab" in buf.getvalue()
    assert "done" in buf.getvalue()


def test_tool_started_spinner_path_non_terminal_falls_back_to_dim_line():
    console, buf = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    repl.render_event(ToolStarted(name="fetch_url", args_summary='"url"'))
    out = buf.getvalue()
    assert "fetch_url" in out and '"url"' in out
    # spinner Live must NOT be active on non-terminal consoles
    assert repl._live is None


def test_error_panel_through_event_renderer_override():
    console, buf = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    repl.render_event(Error(code="EXECUTE_FAILED", message="boom", recoverable=False))
    out = buf.getvalue()
    assert "Error" in out and "EXECUTE_FAILED" in out and "boom" in out
    assert "Recoverable: no" in out


def test_final_renders_markdown_panel_via_render_final():
    console, buf = make_console()
    core = MockCore(script=[Final(text="# Heading\nBody")])
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        await repl.run_turn("x")

    asyncio.run(scenario())
    md = repl.renderer.render_final("**bold**")
    assert "bold" in str(md.markup)


def test_streaming_live_path_on_forced_terminal_console():
    console, buf = make_console(force_terminal=True)
    core = MockCore(script=[Delta(text="live "), Delta(text="stream"), Final(text="ok")])
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        await repl.run_turn("go")

    asyncio.run(scenario())
    out = buf.getvalue()
    assert "live " in out and "stream" in out and "ok" in out
    assert repl._live is None  # live closed at turn end


# --------------------------------------------------------------------- #
# Approval card inline + y/n/a gate
# --------------------------------------------------------------------- #


def test_approval_card_inline_and_gate():
    console, buf = make_console()
    core = MockCore(script=[ApprovalRequested(approval_id="APR-9", reason="rm build cache")])
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        repl._input_provider = feed("a")
        await repl.run_turn("clean")

    asyncio.run(scenario())
    out = buf.getvalue()
    assert "Approval Required" in out      # ApprovalCard panel title
    assert "APR-9" in out                  # card carries the id inline
    assert "approved" in out
    assert ("APR-9", True, "operator") in core.resolutions


def test_approval_rejected_via_n():
    console, buf = make_console()
    core = MockCore(script=[ApprovalRequested(approval_id="APR-10", reason="wipe")])
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        repl._input_provider = feed("n")
        await repl.run_turn("go")

    asyncio.run(scenario())
    assert ("APR-10", False, "operator") in core.resolutions
    assert "rejected" in buf.getvalue()


# --------------------------------------------------------------------- #
# Slash commands, palette, autocomplete
# --------------------------------------------------------------------- #


def test_slash_palette_opens_on_bare_slash():
    console, buf = make_console()
    core = MockCore()
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        repl._input_provider = feed("/", "/exit")
        await repl.run()

    asyncio.run(scenario())
    out = buf.getvalue()
    assert "Command Palette" in out
    for cmd in ("/help", "/clear", "/exit"):
        assert cmd in out
    assert core.received == []


def test_complete_slash_prefix_expansion():
    repl = TerminalREPL(core=MockCore(), console=make_console()[0], banner=False)
    assert repl.complete_slash("/ex") == "/exit"
    assert repl.complete_slash("/") == "/"          # ambiguous: no change
    assert repl.complete_slash("/he") == "/help"
    assert repl.complete_slash("/nope") == "/nope"  # no match: unchanged
    assert repl.complete_slash("hello") == "hello"  # non-slash untouched
    assert repl.complete_slash("/help now") == "/help now"


def test_tab_completion_keybinding_wired():
    captured: list[str] = []

    class FakeBuffer:
        text = "/he"
        cursor_position = 3

    class FakeApp:
        current_buffer = FakeBuffer()

    class FakeEvent:
        app = FakeApp()

    def tab(event):
        event.app.current_buffer.text = event.app.current_buffer.text.replace(
            "/he", "/help"
        )
        captured.append(event.app.current_buffer.text)

    kb = build_key_bindings(
        on_send=lambda e: None,
        on_interrupt=lambda e: None,
        on_eof=lambda e: None,
        on_tab_complete=tab,
        on_clear=lambda e: None,
    )
    bound_keys = {tuple(b.keys) for b in kb.bindings}
    assert ("c-i",) in bound_keys or ("tab",) in bound_keys  # Tab == Ctrl+I
    assert ("c-m",) in bound_keys or ("enter",) in bound_keys  # Enter == Ctrl+M
    assert ("escape", "c-m") in bound_keys or ("escape", "enter") in bound_keys  # Alt+Enter newline
    assert ("c-c",) in bound_keys             # interrupt turn
    assert ("c-l",) in bound_keys             # clear screen
    assert ("c-d",) in bound_keys             # exit

    for b in kb.bindings:
        if tuple(b.keys) == ("c-i",) or tuple(b.keys) == ("tab",):
            b.handler(FakeEvent())
    assert captured == ["/help"]
    assert captured == ["/help"]
    assert set(SLASH_COMMANDS) == {"/help", "/clear", "/exit", "/quit"}


# --------------------------------------------------------------------- #
# Banner health summary
# --------------------------------------------------------------------- #


def test_banner_shows_version_provider_model():
    console, buf = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    repl.print_banner(summary=("1.2.3-test", "copilot", "gpt-4o"))
    out = buf.getvalue()
    assert "1.2.3-test" in out
    assert "copilot" in out
    assert "gpt-4o" in out
    assert "version" in out and "provider" in out and "model" in out


def test_collect_health_summary_never_raises():
    version, provider, model = TerminalREPL.collect_health_summary()
    assert all(isinstance(x, str) and x for x in (version, provider, model))


def test_construction_with_banner_true_prints_nothing():
    console, buf = make_console()
    TerminalREPL(core=MockCore(), console=console, banner=True)
    assert buf.getvalue() == ""


# --------------------------------------------------------------------- #
# Interrupt behavior
# --------------------------------------------------------------------- #


def test_interrupt_cancels_turn_and_reports_cancelled():
    console, buf = make_console()

    class SlowCore(MockCore):
        async def handle(self, msg):
            self.received.append(msg)
            yield Delta(text="partial")
            await asyncio.sleep(30)

    core = SlowCore()
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        task = asyncio.create_task(repl.run_turn("long mission"))
        await asyncio.sleep(0.05)
        task.cancel()  # Ctrl+C proxy
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    out = buf.getvalue().lower()
    assert "partial" in out
    assert "cancelled" in out
