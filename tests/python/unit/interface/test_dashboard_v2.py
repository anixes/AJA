"""Tests for AJADashboard v2 (mockup layout) — headless via Textual run_test()."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List

import pytest

from aja.core.events import ApprovalRequested, Delta, Final, ToolFinished, ToolStarted
from aja.messaging.envelope import InboundMessage
from aja.tui.dashboard import AJADashboard, ApprovalCard


class MockCore:
    """Records InboundMessages and replays canned CoreEvents."""

    def __init__(self, events: List[Any]) -> None:
        self.events = events
        self.messages: List[InboundMessage] = []
        self._gate = asyncio.Event()

    async def handle(self, msg: InboundMessage) -> AsyncIterator[Any]:
        self.messages.append(msg)
        for ev in self.events:
            yield ev
        self._gate.set()


def _make_app(events: List[Any] | None = None, **providers) -> AJADashboard:
    defaults: Dict[str, Any] = {
        "health_check": lambda: [{"name": "python", "ok": True, "detail": "3.12"}],
        "focus_refresh": lambda: [
            {"priority_score": 94, "urgency_tier": "critical", "title": "overdue Bill", "due_label": "overdue"},
            {"priority_score": 81, "urgency_tier": "high", "title": "Email Dana"},
            {"priority_score": 67, "urgency_tier": "high", "title": "CV rewrite", "due_label": "fri"},
        ],
        "missions_refresh": lambda: [
            {"mission_id": "m_a1f2c3d4", "goal": "research python versions", "status": "ACTIVE"}
        ],
        "day_refresh": lambda: [{"goal": "standup notes", "schedule_expr": "at:2026-08-24T09:00", "paused": False}],
        "model_info": "hybrid · gpt-4o",
    }
    defaults.update(providers)
    return AJADashboard(core=MockCore(events or []), **defaults)


async def _wait_turn(app: AJADashboard) -> None:
    assert app._turn_task is not None
    await asyncio.wait_for(app._turn_task, timeout=5.0)


def _log_text(app: AJADashboard) -> str:
    return "\n".join(str(line) for line in app.query_one("#chat-log").lines)


# ------------------------------------------------------------------ #
# 1. Mount / layout
# ------------------------------------------------------------------ #


@pytest.mark.anyio
async def test_mount_without_error_renders_mockup_layout() -> None:
    app = _make_app()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert app.is_running
        app.query_one("#chat-log")
        app.query_one("#status-line")
        app.query_one("#approval-dock")
        app.query_one("#sidebar-tabs")
        app.query_one("#focus-list")
        app.query_one("#missions-list")
        app.query_one("#day-list")
        app.query_one("#command-input")
        tabs = app.query_one("#sidebar-tabs")
        assert tabs.active == "focus-pane"
        # Header carries health summary + model info.
        assert "1/1 checks" in str(app.sub_title)
        assert "hybrid · gpt-4o" in str(app.sub_title)
        # Input is focused after mount.
        assert app.focused is app.query_one("#command-input")


@pytest.mark.anyio
async def test_sidebar_tabs_populate_from_providers() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        focus = str(app.query_one("#focus-list").render())
        missions = str(app.query_one("#missions-list").render())
        day = str(app.query_one("#day-list").render())
        assert "94" in focus and "overdue Bill" in focus
        assert "m_a1f2c3d4" in missions and "research" in missions
        assert "standup notes" in day


@pytest.mark.anyio
async def test_sidebar_error_provider_degrades_gracefully() -> None:
    def boom() -> List[Dict[str, Any]]:
        raise RuntimeError("lance down")

    app = _make_app(missions_refresh=boom)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = str(app.query_one("#missions-list").render())
        assert "lance down" in text
        assert app.is_running


# ------------------------------------------------------------------ #
# 2. Chat pipeline
# ------------------------------------------------------------------ #


@pytest.mark.anyio
async def test_input_submission_feeds_core_inbound_message() -> None:
    core = MockCore([Final(text="here is your plate")])
    app = AJADashboard(
        core=core,
        health_check=lambda: [],
        focus_refresh=lambda: [],
        missions_refresh=lambda: [],
        day_refresh=lambda: [],
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "enter")
        await _wait_turn(app)
        await pilot.pause()
        assert len(core.messages) == 1
        msg = core.messages[0]
        assert (msg.surface, msg.chat_id, msg.user_id, msg.text) == (
            "tui",
            "tui-dashboard",
            "operator",
            "hi",
        )
        assert "hi" in _log_text(app)


@pytest.mark.anyio
async def test_events_render_incrementally() -> None:
    events = [
        Delta(text="partial answer"),
        ToolStarted(name="search_web", args_summary='{"q":"python"}'),
        ToolFinished(name="search_web", success=True, duration_ms=412.0),
        Final(text="# Done\nAll good."),
    ]
    app = _make_app(events)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("g", "o", "enter")
        await _wait_turn(app)
        await pilot.pause()
        rendered = _log_text(app)
        assert "search_web" in rendered
        assert "✓ search_web" in rendered and "412ms" in rendered
        assert "Done" in rendered  # final markdown panel
        # status line cleared after turn completes
        assert app.query_one("#status-line").display is False


@pytest.mark.anyio
async def test_approval_card_renders_and_resolves() -> None:
    resolved: List[tuple[str, bool]] = []

    def resolver(approval_id: str, approved: bool) -> None:
        resolved.append((approval_id, approved))

    events = [ApprovalRequested(approval_id="ap_42", reason="rm -rf build/"), Final(text="done")]
    app = _make_app(events, approval_resolver=resolver)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("g", "o", "enter")
        await _wait_turn(app)
        await pilot.pause()
        card = app.query_one(ApprovalCard)
        assert "ap_42" in str(card.query_one(".approval-text").render())
        card.query_one("#approve").press()
        await pilot.pause()
        assert resolved == [("ap_42", True)]
        assert app._resolved_approvals == {"ap_42": True}
        assert "approved" in str(card.query_one(".approval-text").render())


@pytest.mark.anyio
async def test_slash_command_routes_to_binding_action() -> None:
    opened: List[bool] = []

    class SpyDashboard(AJADashboard):
        async def action_briefing_screen(self) -> None:
            opened.append(True)

    app = SpyDashboard(
        core=MockCore([]),
        health_check=lambda: [],
        briefing_fn=lambda: "# brief",
        focus_refresh=lambda: [],
        missions_refresh=lambda: [],
        day_refresh=lambda: [],
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/", "b", "enter")  # "/b…" unknown → system line
        await pilot.pause()
        assert "unknown command" in _log_text(app)
        app.query_one("#command-input").value = "/briefing"
        await pilot.press("enter")
        await pilot.pause()
        assert opened == [True]


# ------------------------------------------------------------------ #
# 3. Bindings & overlays
# ------------------------------------------------------------------ #


@pytest.mark.anyio
async def test_f2_switches_sidebar_to_missions() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()
        assert app.query_one("#sidebar-tabs").active == "missions-pane"


@pytest.mark.anyio
async def test_f1_opens_help_overlay_then_esc_closes() -> None:
    from aja.tui.dashboard import HelpScreen

    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        assert "ctrl+c" in str(app.screen.query_one("#help-box").render())
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


@pytest.mark.anyio
async def test_f3_shows_briefing_modal_with_composed_markdown() -> None:
    from aja.tui.dashboard import BriefingScreen

    app = _make_app(briefing_fn=lambda: "# Daily Briefing\n- task A")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, BriefingScreen)
        body = str(app.screen.query_one("Markdown").source)
        assert "Daily Briefing" in body
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, BriefingScreen)


@pytest.mark.anyio
async def test_s_key_cycles_skins() -> None:
    from aja.tui.dashboard import SKINS

    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Blur the input so the app-level "s" binding fires (typing must win).
        app.set_focus(None)
        await pilot.pause()
        for expected in SKINS[1:] + SKINS[:1]:
            await pilot.press("s")
            await pilot.pause()
            assert f"-skin-{expected}" in app.classes
        # typing "s" into the chat input does NOT change skins
        app.query_one("#command-input").focus()
        skin_before = str(app.classes)
        await pilot.press("s")
        await pilot.pause()
        assert str(app.classes) == skin_before
        assert app.query_one("#command-input").value == "s"


@pytest.mark.anyio
async def test_ctrl_q_quits_app() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert not app.is_running


@pytest.mark.anyio
async def test_tab_cycles_focus_between_panels() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.focused
        await pilot.press("tab")
        second = app.focused
        assert first is not None and second is not None
        assert second is not first


@pytest.mark.anyio
async def test_ctrl_c_interrupts_running_turn() -> None:
    class SlowCore(MockCore):
        async def handle(self, msg):  # never finishes on its own
            self.messages.append(msg)
            yield Delta(text="working…")
            await asyncio.Event().wait()

    app = AJADashboard(
        core=SlowCore([]),
        health_check=lambda: [],
        focus_refresh=lambda: [],
        missions_refresh=lambda: [],
        day_refresh=lambda: [],
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("g", "o", "enter")
        await pilot.pause()
        assert app._turn_task is not None and not app._turn_task.done()
        assert app.query_one("#status-line").display is True
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._turn_task.done()
        assert "interrupted" in _log_text(app)
