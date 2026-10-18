"""
Workspace Locking Mechanism for AJA Core.
Prevents concurrent runner instances from colliding on the same workspace.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class WorkspaceLockError(Exception):
    """Raised when a workspace cannot be locked because another live instance holds it."""
    pass


def is_pid_alive(pid: int) -> bool:
    """
    Check if a process with the given PID is currently alive on the system.
    Handles Windows (ctypes OpenProcess + GetExitCodeProcess) and POSIX (os.kill).
    Properly handles EPERM (PermissionError) on POSIX: if the process exists but
    belongs to another user, it is ALIVE.
    """
    if pid <= 0:
        return False

    # 1. Try psutil if installed for high fidelity
    try:
        import psutil
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.AccessDenied:
            # Process exists but we lack permissions to inspect it -> ALIVE
            return True
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return False
        except Exception:
            pass  # Fall through to platform-specific checks
    except ImportError:
        pass

    # 2. Windows fallback using kernel32
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            ERROR_ACCESS_DENIED = 5
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                # If OpenProcess failed due to access denied, the process exists!
                err = kernel32.GetLastError()
                if err == ERROR_ACCESS_DENIED:
                    return True
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    # 3. POSIX fallback using os.kill(pid, 0)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # Process exists but we don't have permission to signal it -> ALIVE
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


class WorkspaceLock:
    """
    Acquires an exclusive OS-level and metadata-backed lock on a workspace.
    Guarantees cleanup on normal termination, context manager exit, and atexit.
    Automatically detects and reclaims stale locks left by crashed processes.
    """

    LOCK_FILE = ".aja/workspace.lock"

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        objective: Optional[str] = None,
        force: bool = False,
    ):
        if workspace_root is None:
            from aja.config import PROJECT_ROOT
            self.workspace_root = Path(PROJECT_ROOT).resolve()
        else:
            self.workspace_root = Path(workspace_root).resolve()

        self.lock_path = self.workspace_root / self.LOCK_FILE
        self.objective = objective or ""
        self.force = force
        self.acquired = False
        self._fd: Optional[int] = None
        self._atexit_registered = False

    def _lock_fd(self, fd: int) -> bool:
        """Attempt non-blocking exclusive OS-level kernel lock on the file descriptor."""
        try:
            if sys.platform == "win32":
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, IOError):
            return False

    def _unlock_fd(self, fd: int) -> None:
        """Release OS-level kernel lock."""
        try:
            if sys.platform == "win32":
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except (OSError, IOError):
            pass

    def read_lock_info(self) -> Optional[Dict[str, Any]]:
        """Read existing lock metadata from file, if any."""
        if self._fd is not None:
            try:
                os.lseek(self._fd, 0, os.SEEK_SET)
                raw = os.read(self._fd, 8192).decode("utf-8").strip()
                return json.loads(raw) if raw else None
            except Exception:
                return None

        if not self.lock_path.exists():
            return None
        try:
            content = self.lock_path.read_text(encoding="utf-8").strip()
            if not content:
                return None
            return json.loads(content)
        except Exception as e:
            logger.debug("Failed to read workspace lock metadata: %s", e)
            return None


    def acquire(self) -> None:
        """
        Acquires the workspace lock.
        Raises WorkspaceLockError if another live process holds the lock.
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        existing_info = self.read_lock_info()
        if existing_info and not self.force:
            existing_pid = existing_info.get("pid")
            created_at = existing_info.get("created_at", "unknown")
            host = existing_info.get("hostname", "unknown")

            if existing_pid and existing_pid != os.getpid() and is_pid_alive(existing_pid):
                raise WorkspaceLockError(
                    f"Workspace '{self.workspace_root}' is locked by another running AJA instance "
                    f"(PID: {existing_pid}, host: {host}, started: {created_at}).\n"
                    f"If this is a stale lock from a dead process, delete '{self.lock_path}' or use --force."
                )
            elif existing_pid and not is_pid_alive(existing_pid):
                logger.warning(
                    "[WorkspaceLock] Reclaiming stale lockfile from deceased PID %d (started %s)",
                    existing_pid,
                    created_at,
                )

        # Open file for read/write with create
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY

        try:
            fd = os.open(str(self.lock_path), flags, 0o644)
        except OSError as exc:
            raise WorkspaceLockError(f"Could not open workspace lockfile '{self.lock_path}': {exc}")

        # Attempt kernel-level advisory lock
        if not self._lock_fd(fd):
            os.close(fd)
            # Another process locked the file at kernel level
            info = self.read_lock_info() or {}
            locked_pid = info.get("pid", "unknown")
            raise WorkspaceLockError(
                f"Workspace '{self.workspace_root}' OS lock is currently held by PID {locked_pid}."
            )

        self._fd = fd
        self.acquired = True

        # Write current process metadata into lockfile
        import socket
        metadata = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "objective": self.objective[:300] if self.objective else "",
        }
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            payload = json.dumps(metadata, indent=2).encode("utf-8")
            os.write(fd, payload)
            os.fsync(fd)
        except OSError as e:
            logger.warning("[WorkspaceLock] Failed to write lock metadata: %s", e)

        # Register cleanup on exit
        if not self._atexit_registered:
            atexit.register(self.release)
            self._atexit_registered = True

    def release(self) -> None:
        """Releases the workspace lock and unlinks the lockfile."""
        if not self.acquired:
            return

        if self._fd is not None:
            try:
                self._unlock_fd(self._fd)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

        try:
            info = self.read_lock_info()
            if info and info.get("pid") == os.getpid():
                if self.lock_path.exists():
                    self.lock_path.unlink()
        except OSError:
            pass
        finally:
            self.acquired = False

    def __enter__(self) -> WorkspaceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
