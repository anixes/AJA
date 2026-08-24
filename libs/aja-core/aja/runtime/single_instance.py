"""Single-instance guards for long-running AJA processes.

Prevents the duplicate-daemon class of failures (e.g. six concurrent
gateway servers fighting over Telegram's getUpdates, where a stale
zombie instance consumes updates and silently drops messages).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional, Union


def _lock_path(name: str) -> Path:
    from aja.config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{name}.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def is_running(name: str) -> bool:
    """True when a live process currently holds the ``name`` lock."""
    lock = _lock_path(name)
    try:
        if not lock.exists():
            return False
        existing_pid = int(lock.read_text(encoding="utf-8").strip().split(":")[0])
        return existing_pid != os.getpid() and _pid_alive(existing_pid)
    except (OSError, ValueError):
        return False


def acquire_lock(name: str) -> Optional[Path]:
    """Claims the single-instance lock for ``name``.

    Returns the lock path on success, or None when another live process
    already holds it (stale locks from dead PIDs are reclaimed).
    """
    lock = _lock_path(name)
    if is_running(name):
        return None
    try:
        lock.write_text(f"{os.getpid()}:{int(time.time())}", encoding="utf-8")
        return lock
    except OSError:
        # Never block startup on lock I/O failure — better a rare duplicate
        # than a gateway that cannot start at all.
        return lock


def release_lock(lock: Optional[Union[str, Path]]) -> None:
    """Releases the lock if it is still owned by this process (best-effort)."""
    if lock is None:
        return
    try:
        path = Path(lock)
        if path.exists() and path.read_text(
            encoding="utf-8"
        ).strip().startswith(f"{os.getpid()}:"):
            path.unlink()
    except OSError:
        pass
