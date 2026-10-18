"""Unit tests for TerminalREPL rendering + loop plumbing.

All turns run against a mocked ConversationCore that yields scripted events;
output is captured through a rich Console bound to io.StringIO.
"""
from __future__ import annotations

import asyncio
import io
from typing import AsyncIterator

import pytest
from rich.console import Console

from aja.core.events import (
    ApprovalRequested,
    Delta,
    Error,
    Final,
    ToolFinished,
    ToolStarted,
)
from aja.interface.repl import TerminalREPL
from aja.messaging.envelope import InboundMessage


def make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200, legacy_windows=False)
    return console, buf


class MockCore:
    """Scripted ConversationCore double."""

    def __init__(self, script: list | None = None) -> None:
        self.script = script or []
        self.received: list[InboundMessage] = []
        self.resolutions: list[tuple[str, bool, str]] = []
        self._approval_answer = "y"

    async def handle(self, msg: InboundMessage) -> AsyncIterator:
        self.received.append(msg)
        for ev in self.script:
            yield ev

    async def resolve_approval(self, approval_id: str, approved: bool, approver_id: str = "") -> dict:
        self.resolutions.append((approval_id, approved, approver_id))
        return {"status": "ok", "approved": approved}


def feed(*lines: str):
    """Input provider returning scripted lines then EOF."""

    queue = list(lines)

    async def provider(prompt: str = "") -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return provider


# --------------------------------------------------------------------- #
# Rendering units
# --------------------------------------------------------------------- #


def test_render_delta_prints_inline_without_newline():
    console, buf = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    repl.render_event(Delta(text="Hel"))
    repl.render_event(Delta(text="lo"))
    assert "Hello" in buf.getvalue()


def test_render_tool_started_is_dim_line():
    console, buf = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    repl.render_event(ToolStarted(name="search_web", args_summary='{"q": "py"}'))
    out = buf.getvalue()
    assert "search_web" in out and '{"q": "py"}' in out


def test_render_tool_finished_success_and_failure_marks():
    console, buf = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    repl.render_event(ToolFinished(name="fetch_url", success=True, duration_ms=123.4))
    repl.render_event(ToolFinished(name="shell", success=False, duration_ms=5))
    out = buf.getvalue()
    assert "✔" in out and "✘" in out
    assert "123ms" in out


def test_render_error_boxed_panel_with_code_and_message():
    console, buf = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    repl.render_event(Error(code="EXECUTE_FAILED", message="boom", recoverable=False))
    out = buf.getvalue()
    assert "Error" in out and "EXECUTE_FAILED" in out and "boom" in out
    assert "no" in out  # recoverable=no


def test_render_final_markdown_panel():
    console, buf = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    repl.render_event(Final(text="**Done** — all green"))
    out = buf.getvalue()
    assert "AJA" in out and "Done" in out and "all green" in out


# --------------------------------------------------------------------- #
# Loop behavior
# --------------------------------------------------------------------- #


def test_run_delivers_inbound_message_and_renders_scripted_events():
    console, buf = make_console()
    core = MockCore(
        script=[
            Delta(text="thinking"),
            ToolStarted(name="calc", args_summary="2+2"),
            ToolFinished(name="calc", success=True, duration_ms=1.0),
            Final(text="The answer is **4**."),
        ]
    )
    repl = TerminalREPL(core=core, console=console, banner=False)
    asyncio.run(_run_one_turn(repl))

    assert len(core.received) == 1
    msg = core.received[0]
    assert isinstance(msg, InboundMessage)
    assert msg.surface == "cli" and msg.text == "hello there"
    out = buf.getvalue()
    assert "thinking" in out
    assert "calc" in out
    assert "✔" in out
    assert "The answer is" in out


async def _run_one_turn(repl: TerminalREPL) -> None:
    await repl.run_turn("hello there")


def test_slash_commands_handled_locally():
    console, buf = make_console()
    core = MockCore()
    repl = TerminalREPL(core=core, console=console, banner=False)
    asyncio.run(_run_with_input(repl, ["/help", "/clear", "/bogus", "/exit"]))

    out = buf.getvalue()
    assert "/help" in out or "Commands" in out
    assert "Unknown command" in out
    assert core.received == []  # slash commands never hit the core
    assert "Goodbye." in buf.getvalue()


async def _run_with_input(repl: TerminalREPL, lines: list[str]) -> None:
    repl._input_provider = feed(*lines)
    await repl.run()


def test_empty_input_does_not_call_core():
    console, _ = make_console()
    core = MockCore(script=[Final(text="x")])
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        repl._input_provider = feed("", "   ", "/exit")
        await repl.run()

    asyncio.run(scenario())
    assert core.received == []


def test_approval_flow_resolves_via_core():
    console, buf = make_console()
    core = MockCore(
        script=[ApprovalRequested(approval_id="APR-1", reason="run rm -rf build/tmp")]
    )
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        repl._input_provider = feed("run the dangerous thing")
        async def answer(prompt=""):
            return "a"
        repl._input_provider = answer
        await repl.run_turn("go")

    asyncio.run(scenario())
    assert len(core.resolutions) == 1
    approval_id, approved, approver = core.resolutions[0]
    assert approval_id == "APR-1"
    assert approved is True  # 'a' (always) counts as approve
    assert approver == repl._user_id
    assert "approved" in buf.getvalue()


def test_approval_rejected_on_n():
    console, buf = make_console()
    core = MockCore(script=[ApprovalRequested(approval_id="APR-2", reason="delete files")])
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def scenario():
        async def answer(prompt=""):
            return "n"
        repl._input_provider = answer
        await repl.run_turn("go")

    asyncio.run(scenario())
    assert core.resolutions == [("APR-2", False, "operator")]
    assert "rejected" in buf.getvalue()


def test_injected_approval_resolver_short_circuits_prompt():
    console, buf = make_console()
    core = MockCore(script=[ApprovalRequested(approval_id="APR-3", reason="deploy")])
    repl = TerminalREPL(core=core, console=console, banner=False)

    async def auto_approve(ev):
        return True

    repl._approval_resolver = auto_approve

    async def scenario():
        await repl.run_turn("ship it")

    asyncio.run(scenario())
    assert ("APR-3", True, "operator") in core.resolutions


def test_turn_cancelled_on_keyboard_interrupt():
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
        task.cancel()  # Ctrl+C proxy: external cancellation of the turn
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    out = buf.getvalue().lower()
    assert "partial" in out
    assert "cancelled" in out


def test_lazy_core_property_builds_only_once():
    repl = TerminalREPL(core=None, console=make_console()[0], banner=False)
    sentinel = MockCore()
    repl._core = sentinel
    assert repl.core is sentinel


def test_banner_disabled_by_default_kwarg():
    console, buf = make_console()
    TerminalREPL(core=MockCore(), console=console, banner=True)
    # Banner only prints inside run(); direct construction must not write.
    assert buf.getvalue() == ""
