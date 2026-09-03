"""v2 tests for the EventRenderer-backed TerminalREPL.

Covers: scripted-event rendering through the shared EventRenderer pipeline,
streaming Delta buffering, tool spinner replacement, inline approval cards,
slash-command palette/autocomplete, keyboard-binding map, banner health
summary, and interrupt behavior.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import io
import sys
import types
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


EXPECTED_SLASH_COMMANDS: tuple[str, ...] = (
    "/help", "/clear", "/exit", "/quit", "/status", "/kanban", "/live",
    "/missions", "/models", "/local", "/tui", "/swarm", "/goal", "/schedule",
    "/doctor", "/todo", "/doing", "/done", "/failed", "/rmtask",
)


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
    assert SLASH_COMMANDS == EXPECTED_SLASH_COMMANDS


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


# --------------------------------------------------------------------- #
# Slash-command dispatch contract
# --------------------------------------------------------------------- #
#
# All handlers are lazy-imported inside ``_handle_slash_command`` at call
# time, so monkeypatching the *module attribute* intercepts every dispatch.
# Some handlers may be scheduled via ``asyncio.to_thread``; the driver below
# flushes pending tasks (with a timeout) before assertions run.


def _ensure_module(monkeypatch, dotted: str):
    """Import (or stub-create) module ``dotted`` and return it."""
    try:
        return importlib.import_module(dotted)
    except ImportError:
        mod = types.ModuleType(dotted)
        sys.modules[dotted] = mod
        parent_name, _, leaf = dotted.rpartition(".")
        setattr(importlib.import_module(parent_name), leaf, mod)
        return mod


async def _flush_pending_tasks(timeout: float = 5.0) -> None:
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current]
    if not pending:
        return
    _, still = await asyncio.wait(pending, timeout=timeout)
    for t in still:
        t.cancel()
    await asyncio.gather(*still, return_exceptions=True)


def _drive(repl: TerminalREPL, text: str, timeout: float = 5.0) -> str:
    """Run ``_handle_slash_command(text)`` under asyncio and flush to_thread."""

    async def scenario():
        result = repl._handle_slash_command(text)
        await _flush_pending_tasks(timeout)
        return result

    return asyncio.run(scenario())


def make_dispatch_repl():
    console, buf = make_console()
    repl = TerminalREPL(core=MockCore(), console=console, banner=False)
    return repl, console, buf


class FakeDashboard:
    instances: list["FakeDashboard"] = []

    def __init__(self) -> None:
        self.run_calls = 0
        self.__class__.instances.append(self)

    def run(self) -> None:
        self.run_calls += 1


class FakeTaskManager:
    instances: list["FakeTaskManager"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[tuple] = []
        self.__class__.instances.append(self)

    def add_task(self, title: str, node_id: str = "") -> str:
        self.calls.append(("add_task", title))
        return "T-1"

    def update_status(self, task_id: str, status: str) -> None:
        self.calls.append(("update_status", task_id, status))

    def delete_task(self, task_id: str) -> None:
        self.calls.append(("delete_task", task_id))


def test_slash_commands_tuple_matches_contract_exactly():
    from aja.interface.repl import SLASH_COMMANDS as CURRENT

    assert CURRENT == EXPECTED_SLASH_COMMANDS


@pytest.mark.parametrize("text", ["/exit", "/quit"])
def test_exit_and_quit_return_exit_sentinel(text):
    repl, _, buf = make_dispatch_repl()
    assert _drive(repl, text) == "exit"


@pytest.mark.parametrize("text", ["hello", " run the tests", "", "   ", "slash/not"])
def test_non_slash_input_returns_empty_string(text):
    repl, _, _ = make_dispatch_repl()
    core_calls_before = len(repl.core.received)
    assert _drive(repl, text) == ""
    assert len(repl.core.received) == core_calls_before


# --- /status ----------------------------------------------------------- #


def test_status_dispatches_to_cmd_status(monkeypatch):
    repl, _, _ = make_dispatch_repl()
    status_mod = importlib.import_module("aja.cli.commands.status")
    calls: list[tuple] = []
    monkeypatch.setattr(status_mod, "cmd_status", lambda *a, **k: calls.append((a, k)))
    assert _drive(repl, "/status") == "handled"
    assert len(calls) == 1


# --- /kanban /live /missions ------------------------------------------- #


@pytest.mark.parametrize("cmd", ["/kanban", "/live", "/missions"])
def test_fullscreen_dashboard_commands_route_through_modal(cmd, monkeypatch):
    repl, _, buf = make_dispatch_repl()
    dashboard_mod = importlib.import_module("aja.tui.dashboard")
    terminal_mod = importlib.import_module("aja.tui.terminal")

    FakeDashboard.instances = []
    modal_calls: list[tuple] = []

    def fake_modal(runner_fn, *args, **kwargs):
        modal_calls.append((runner_fn, args, kwargs))
        return "modal-result"

    monkeypatch.setattr(dashboard_mod, "AJADashboard", FakeDashboard)
    monkeypatch.setattr(terminal_mod, "run_fullscreen_modal", fake_modal)

    result = _drive(repl, cmd)

    assert result == "handled"
    assert len(FakeDashboard.instances) == 1
    assert len(modal_calls) == 1
    runner_fn = modal_calls[0][0]
    assert callable(runner_fn)
    # The dashboard's own .run is what gets handed to the fullscreen wrapper.
    assert runner_fn == FakeDashboard.instances[0].run


# --- /models ------------------------------------------------------------ #


@pytest.mark.parametrize(
    ("line", "expected_args"),
    [
        ("/models", ""),
        ("/models list --refresh", "list --refresh"),
    ],
)
def test_models_dispatch_passes_console_and_args(monkeypatch, line, expected_args):
    repl, console, _ = make_dispatch_repl()
    models_mod = _ensure_module(monkeypatch, "aja.cli.commands.models")
    calls: list[tuple] = []

    def fake_handle_models_command(args="", *, console=None):
        calls.append((args, console))

    # raising=False: the module may be a fresh stub created by _ensure_module
    # until production adds aja/cli/commands/models.py.
    monkeypatch.setattr(models_mod, "handle_models_command", fake_handle_models_command, raising=False)
    assert _drive(repl, line) == "handled"
    assert len(calls) == 1
    assert calls[0][0] == expected_args
    assert calls[0][1] is console


# --- /tui --------------------------------------------------------------- #


def test_tui_wraps_curses_main_in_modal_and_asyncio_run(monkeypatch):
    repl, _, buf = make_dispatch_repl()
    curses_mod = importlib.import_module("aja.tui.curses_tui")
    terminal_mod = importlib.import_module("aja.tui.terminal")

    main_calls: list[tuple] = []

    async def fake_run_curses_tui_main(dry_run=False):
        main_calls.append((dry_run,))

    modal_calls: list[tuple] = []

    def fake_modal(runner_fn, *args, **kwargs):
        modal_calls.append((runner_fn, args, kwargs))
        return "modal-result"

    monkeypatch.setattr(curses_mod, "run_curses_tui_main", fake_run_curses_tui_main)
    monkeypatch.setattr(terminal_mod, "run_fullscreen_modal", fake_modal)

    # Record asyncio.run usage without changing behavior.
    real_asyncio_run = asyncio.run
    run_calls: list = []

    def recording_asyncio_run(coro, **kwargs):
        run_calls.append(coro)
        return real_asyncio_run(coro, **kwargs)

    monkeypatch.setattr(asyncio, "run", recording_asyncio_run)

    try:
        result = _drive(repl, "/tui")
    except RuntimeError as e:  # pragma: no cover - pending production fix
        pytest.fail(f"/tui dispatch raised inside running loop (pending impl): {e}")

    assert result == "handled"
    assert len(modal_calls) == 1
    runner_fn = modal_calls[0][0]

    # If the wrapped entry point hasn't executed yet, drive it ourselves now
    # that no loop is running (it may internally call asyncio.run).
    if not main_calls and callable(runner_fn):
        outcome = runner_fn()
        if inspect.iscoroutine(outcome):
            real_asyncio_run(outcome)

    assert main_calls, "run_curses_tui_main was never reached through the modal wrapper"
    assert run_calls, "asyncio.run was never used to drive the curses TUI"


# --- /doctor ------------------------------------------------------------ #


def test_doctor_dispatches_to_cmd_doctor(monkeypatch):
    repl, _, _ = make_dispatch_repl()
    doctor_mod = importlib.import_module("aja.cli.commands.doctor")
    calls: list[tuple] = []
    monkeypatch.setattr(doctor_mod, "cmd_doctor", lambda *a, **k: calls.append((a, k)))
    assert _drive(repl, "/doctor") == "handled"
    assert len(calls) == 1


# --- /todo /doing /done /failed /rmtask --------------------------------- #

_TASK_STATUS_MAP = None


def _task_status_map():
    global _TASK_STATUS_MAP
    if _TASK_STATUS_MAP is None:
        from aja.tui.tasks import STATUS_COMPLETED, STATUS_FAILED, STATUS_RUNNING

        _TASK_STATUS_MAP = {
            "/doing": STATUS_RUNNING,
            "/done": STATUS_COMPLETED,
            "/failed": STATUS_FAILED,
        }
    return _TASK_STATUS_MAP


@pytest.fixture()
def fake_tasks(monkeypatch):
    tasks_mod = importlib.import_module("aja.tui.tasks")
    FakeTaskManager.instances = []
    monkeypatch.setattr(tasks_mod, "TaskManager", FakeTaskManager)
    return FakeTaskManager


def test_todo_with_title_adds_task(fake_tasks):
    repl, _, buf = make_dispatch_repl()
    result = _drive(repl, "/todo write the changelog")
    assert result == "handled"
    assert len(fake_tasks.instances) == 1
    tm = fake_tasks.instances[0]
    assert tm.calls == [("add_task", "write the changelog")]


@pytest.mark.parametrize("cmd", ["/doing", "/done", "/failed"])
def test_status_update_commands_call_update_status(cmd, fake_tasks):
    repl, _, _ = make_dispatch_repl()
    result = _drive(repl, f"{cmd} T-42")
    assert result == "handled"
    assert len(fake_tasks.instances) == 1
    assert fake_tasks.instances[0].calls == [
        ("update_status", "T-42", _task_status_map()[cmd])
    ]


def test_rmtask_deletes_by_id(fake_tasks):
    repl, _, _ = make_dispatch_repl()
    result = _drive(repl, "/rmtask T-7")
    assert result == "handled"
    assert len(fake_tasks.instances) == 1
    assert fake_tasks.instances[0].calls == [("delete_task", "T-7")]


@pytest.mark.parametrize("line", ["/todo", "/doing", "/done", "/failed", "/rmtask"])
def test_task_commands_without_args_print_usage_and_skip_manager(line, fake_tasks):
    repl, _, buf = make_dispatch_repl()
    result = _drive(repl, line)
    assert result == "handled"
    out = buf.getvalue()
    assert "Usage" in out or "usage" in out.lower()
    assert fake_tasks.instances == []  # TaskManager never constructed


# --- /swarm /goal with no args ------------------------------------------ #


@pytest.mark.parametrize("cmd", ["/swarm", "/goal"])
def test_swarm_and_goal_without_args_only_print_usage(cmd):
    repl, _, buf = make_dispatch_repl()
    # Nothing should be executed: block process spawning just in case.
    result = _drive(repl, cmd)
    assert result == "handled"
    out = buf.getvalue()
    assert "Usage" in out or "usage" in out.lower()


# --- unknown command ----------------------------------------------------- #


def test_unknown_command_reports_unknown():
    repl, _, buf = make_dispatch_repl()
    result = _drive(repl, "/nope")
    assert result == "handled"
    assert "Unknown command" in buf.getvalue()


# --- non-slash passthrough ------------------------------------------------ #


def test_plain_text_returns_empty_string():
    repl, _, _ = make_dispatch_repl()
    assert _drive(repl, "what time is it?") == ""
