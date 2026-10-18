"""Legacy AJADashboard tests — updated for the v2 mockup layout.

The dashboard was rewritten around a CHAT + FOCUS/MISSIONS/DAY sidebar layout
(see test_dashboard_v2.py for full coverage). This module keeps only the
backwards-compatibility contract: legacy constructor kwargs must still work.
"""
from __future__ import annotations

import pytest

from aja.tui.dashboard import AJADashboard


@pytest.mark.anyio
async def test_legacy_sidebar_refresh_kwarg_still_supported() -> None:
    """Old callers passing ``sidebar_refresh`` dict still get MISSIONS data."""
    app = AJADashboard(
        core=object(),
        health_check=lambda: [],
        sidebar_refresh=lambda: {
            "active_missions": 2,
            "missions": [{"id": "M-abc123", "goal": "demo goal"}],
            "workers_total": 3,
            "workers_healthy": 2,
        },
        focus_refresh=lambda: [],
        day_refresh=lambda: [],
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        text = str(app.query_one("#missions-list").render())
        assert "M-abc123" in text
        assert "demo goal" in text
        assert app.is_running


@pytest.mark.anyio
async def test_import_surface_stable() -> None:
    from aja.tui.dashboard import ApprovalCard  # noqa: F401

    assert hasattr(AJADashboard, "compose")
