"""
=============================================================================
Unit Test: Terminal Lifecycle & Buffer Suspension
=============================================================================
"""

import sys
import pytest
from unittest.mock import patch, MagicMock
from aja.tui.terminal import (
    is_interactive_tty,
    safe_fullscreen_console,
    run_fullscreen_modal,
)


def test_is_interactive_tty_detection():
    with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True):
        assert is_interactive_tty() is True

    with patch("sys.stdin.isatty", return_value=False):
        assert is_interactive_tty() is False


def test_safe_fullscreen_console_headless_degradation():
    """Ensure non-interactive / headless environment degrades gracefully without ANSI exceptions."""
    with patch("sys.stdin.isatty", return_value=False):
        executed = False
        with safe_fullscreen_console() as c:
            executed = True
            assert c is not None
        assert executed is True


def test_run_fullscreen_modal_execution():
    mock_runner = MagicMock(return_value="modal_done")
    with patch("sys.stdin.isatty", return_value=False):
        result = run_fullscreen_modal(mock_runner, 123, key="value")
        assert result == "modal_done"
        mock_runner.assert_called_once_with(123, key="value")
