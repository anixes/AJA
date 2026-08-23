"""Tests for aja.tui.dashboard — Textual AJADashboard."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List

import pytest

from aja.core.events import Delta, Error, Final, ToolFinished, ToolStarted
from aja.messaging.envelope import InboundMessage
from aja.tui.dashboard import AJADashboard


class MockCore:
    """Records InboundMessages and replays canned CoreEvents."""

    def __init__(self, events: List[Any]) -> None:
        self.events = events
        self.messages: List[InboundMessage] = []
        self._done = asyncio.Event()

    async def handle(self, msg: InboundMessage) -> AsyncIterator[Any]:
        self.messages.append(msg)
        for ev in self.events:
            yield ev
        self._done.set()


def _make_app(events: List[Any], **kw) -> AJADashboard:
    return AJADashboard(
        core=MockCore(events),
        health_check=lambda: [{"name": "python", "ok": True, "detail": "test"}],
        sidebar_refresh=lambda: {
            "active_missions": 2,
            "missions": [{"id": "M-abc123", "goal": "demo goal"}],
            "workers_total": 3,
            "workers_healthy": 2,
        },
        accent_color="magenta",
        **kw,
    )


async def _wait_turn(app: AJADashboard) -> None:
    task = app._turn_task
    assert task is not None
    await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.anyio
async def test_mounts_and_renders_widgets() -> None:
    app = _make_app([])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.is_running
        # All structural widgets present.
        from textual.widgets import Footer, Header, Input, RichLog, Static

        app.query_one("#chat-log", RichLog)
        app.query_one("#sidebar", Static)
        app.query_one("#command-input", Input)
        assert isinstance(app.query_one("Header"), Header)
        assert isinstance(app.query_one("Footer"), Footer)


@pytest.mark.anyio
async def test_input_submission_creates_inbound_message() -> None:
    core = MockCore([Final(text="hi there")])
    app = AJADashboard(core=core, health_check=lambda: [], sidebar_refresh=lambda: {})
    async with app.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#command-input").focus()
        await pilot.press("h", "i", "enter")
        await _wait_turn(app)
        assert len(core.messages) == 1
        msg = core.messages[0]
        assert isinstance(msg, InboundMessage)
        assert msg.text == "hi"
        assert msg.surface == "tui"
        assert msg.chat_id == "tui-dashboard"
        assert msg.user_id == "operator"


@pytest.mark.anyio
async def test_events_render_to_chat_log() -> None:
    events = [
        Delta(text="partial answer"),
        ToolStarted(name="search_web", args_summary='{"q": "python"}'),
        ToolFinished(name="search_web", success=True, duration_ms=12.0),
        Final(text="# Done\nAll good."),
        Error(code="X", message="boom"),
    ]
    app = _make_app(events)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#command-input").focus()
        await pilot.press("h", "i", "enter")
        await _wait_turn(app)
        await pilot.pause()
        log = app.query_one("#chat-log")
        # Welcome line + user echo + delta + tool started + tool finished
        # + final panel + error panel => comfortably more than 4 lines.
        assert len(log.lines) > 4
        rendered = "\n".join(str(line) for line in log.lines)
        assert "partial answer" in rendered
        assert "search_web" in rendered


@pytest.mark.anyio
async def test_sidebar_updates_from_refresh_fn() -> None:
    app = _make_app([])
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar")
        text = str(sidebar.render())
        assert "Active missions" in text or "Missions" in text


@pytest.mark.anyio
async def test_health_check_shown_in_header_subtitle() -> None:
    app = _make_app([])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "1/1 checks" in str(app.sub_title)


@pytest.mark.anyio
async def test_ctrl_c_binding_interrupts_turn() -> None:
    class SlowCore(MockCore):
        async def handle(self, msg):  # never finishes on its own
            self.messages.append(msg)
            yield Delta(text="working…")
            await asyncio.Event().wait()

    app = AJADashboard(
        core=SlowCore([]),
        health_check=lambda: [],
        sidebar_refresh=lambda: {},
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#command-input").focus()
        await pilot.press("g", "o", "enter")
        await pilot.pause()
        assert app._turn_task is not None and not app._turn_task.done()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._turn_task.done()
        rendered = "\n".join(str(line) for line in app.query_one("#chat-log").lines)
        assert "interrupted" in rendered


@pytest.mark.anyio
async def test_ctrl_q_quits_app() -> None:
    app = _make_app([])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert not app.is_running


@pytest.mark.anyio
async def test_tab_cycles_focus() -> None:
    app = _make_app([])
    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.focused
        await pilot.press("tab")
        second = app.focused
        assert first is not None
        assert second is not first
