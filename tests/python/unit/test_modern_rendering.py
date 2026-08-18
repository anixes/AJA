"""
=============================================================================
Unit Test: Modern Visual Design System Rendering
=============================================================================
"""

import pytest
from aja.interface.modern import (
    render_agent_card,
    render_tool_badge,
    render_help_grid,
    print_banner,
    print_doctor,
)


def test_render_agent_card():
    card = render_agent_card(
        "Hello Operator. AJA is operational.",
        model="copilot:gpt-4o",
        role="AJA",
    )
    assert card is not None
    assert "AJA" in str(card.title)
    assert "copilot:gpt-4o" in str(card.subtitle)


def test_render_tool_badge_success():
    badge = render_tool_badge(
        tool_name="git_status",
        success=True,
        execution_ms=15.4,
        data="On branch native-worker-3\nnothing to commit",
    )
    assert badge is not None
    assert "git_status" in str(badge.title)
    assert "SUCCESS" in str(badge.title)


def test_render_tool_badge_failure():
    badge = render_tool_badge(
        tool_name="shell_exec",
        success=False,
        error="Permission denied: /etc/shadow",
    )
    assert badge is not None
    assert "FAILED" in str(badge.title)
    assert "red" in str(badge.border_style)


def test_render_help_grid():
    commands = [
        ("/kanban", "Interactive task board"),
        ("/tui", "Mission Control dashboard"),
        ("/doctor", "Run diagnostics"),
    ]
    grid = render_help_grid(commands)
    assert grid is not None
    assert "AJA Command & Modal Hub" in str(grid.title)
