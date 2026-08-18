"""
aja/tui/terminal.py
===================
Safe Terminal Screen Buffer Lifecycle Management & Cross-Platform Async Input.
Ensures seamless switching between Prompt-Toolkit and full-screen Rich TUI
without console buffer corruption or CPU busy-waiting on Windows and Unix.
"""

import sys
import os
import time
import asyncio
import contextlib
from typing import Callable, Any, Optional

from rich.console import Console

console = Console()


def is_interactive_tty() -> bool:
    """Return True if running in an interactive terminal attached to a TTY."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def read_key(timeout: float = 0.05) -> Optional[str]:
    """
    Read a single keypress in a non-blocking or short-timeout manner.
    Returns standard key names: 'up', 'down', 'left', 'right', 'enter', 'tab', 'escape',
    'backspace', or single character strings like 'a', 'm', 'q', etc.
    """
    if not is_interactive_tty():
        return None

    if sys.platform == "win32":
        import msvcrt
        start = time.time()
        while time.time() - start < timeout:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch in (b"\x00", b"\xe0"):  # Special / Arrow key prefix
                    ch2 = msvcrt.getch()
                    if ch2 == b"H":
                        return "up"
                    elif ch2 == b"P":
                        return "down"
                    elif ch2 == b"K":
                        return "left"
                    elif ch2 == b"M":
                        return "right"
                    elif ch2 == b"S":
                        return "delete"
                    return None
                elif ch == b"\r":
                    return "enter"
                elif ch == b"\t":
                    return "tab"
                elif ch == b"\x1b":
                    return "escape"
                elif ch == b"\x08":
                    return "backspace"
                elif ch == b"\x03":  # Ctrl+C
                    raise KeyboardInterrupt
                else:
                    try:
                        return ch.decode("utf-8")
                    except UnicodeDecodeError:
                        return None
            time.sleep(0.01)
        return None
    else:
        import select
        import tty
        import termios

        fd = sys.stdin.fileno()
        try:
            old_settings = termios.tcgetattr(fd)
        except Exception:
            return None

        try:
            tty.setraw(fd)
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if not rlist:
                return None

            ch = sys.stdin.read(1)
            if ch == "\x1b":
                rlist2, _, _ = select.select([sys.stdin], [], [], 0.02)
                if rlist2:
                    ch2 = sys.stdin.read(1)
                    if ch2 == "[":
                        ch3 = sys.stdin.read(1)
                        if ch3 == "A":
                            return "up"
                        elif ch3 == "B":
                            return "down"
                        elif ch3 == "C":
                            return "right"
                        elif ch3 == "D":
                            return "left"
                return "escape"
            elif ch == "\r" or ch == "\n":
                return "enter"
            elif ch == "\t":
                return "tab"
            elif ch == "\x7f":
                return "backspace"
            elif ch == "\x03":
                raise KeyboardInterrupt
            return ch
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass


@contextlib.contextmanager
def safe_fullscreen_console():
    """
    Context manager that safely switches to the alternate screen buffer,
    hides the cursor, and restores the standard buffer on exit.
    """
    if not is_interactive_tty():
        yield console
        return

    # Enter alternate buffer and hide cursor
    console.show_cursor(False)
    console.print("\x1b[?1049h\x1b[H", end="")
    try:
        yield console
    finally:
        # Exit alternate buffer and show cursor
        console.print("\x1b[?1049l", end="")
        console.show_cursor(True)
        # Ensure screen is clear for subsequent prompts
        sys.stdout.flush()


def run_fullscreen_modal(runner_fn: Callable[..., Any], *args, **kwargs) -> Any:
    """
    Safely runs a full-screen interactive TUI modal function without corrupting
    underlying Prompt-Toolkit or console history state.
    """
    with safe_fullscreen_console():
        return runner_fn(*args, **kwargs)
