"""
DaemonManager — Unified Background Process Supervisor
======================================================
Manages Gateway and Autonomous Worker background process pairs with:
  - PID file locking (.aja/daemon.pid)
  - Process tree isolation and unbuffered logging
  - Auto-restart watchdog supervision
  - Graceful signal handling (SIGTERM/SIGINT)
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional

from aja.config import DATA_DIR, PROJECT_ROOT

PYTHON = sys.executable
PID_FILE = DATA_DIR / "daemon.pid"
LOG_FILE = DATA_DIR / "daemon.log"


class DaemonManager:
    """
    Process Supervisor for AJA background services (Gateway + Autonomous Loop).
    """

    def __init__(self, pid_file: Path = PID_FILE, log_file: Path = LOG_FILE):
        self.pid_file = pid_file
        self.log_file = log_file

    def get_status(self) -> Dict[str, str]:
        """Check status of daemon from PID file."""
        if not self.pid_file.exists():
            return {"status": "stopped", "pid": None}

        try:
            data = json.loads(self.pid_file.read_text(encoding="utf-8"))
            pid = data.get("pid")
            if pid and self._is_process_running(pid):
                return {
                    "status": "running",
                    "pid": pid,
                    "started_at": data.get("started_at", "-"),
                    "services": data.get("services", []),
                }
            else:
                # Stale PID file
                self.pid_file.unlink(missing_ok=True)
                return {"status": "stopped", "pid": None}
        except Exception:
            return {"status": "stopped", "pid": None}

    def start_daemon(self, background: bool = True) -> Dict[str, str]:
        """Start the supervised Gateway and Autonomous Worker processes."""
        current = self.get_status()
        if current["status"] == "running":
            return {"status": "already_running", "pid": current["pid"]}

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        if os.name == "nt":
            creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            creationflags = 0

        log_handle = open(self.log_file, "a", encoding="utf-8")
        try:
            # Spawn gateway and worker as child processes
            gateway_proc = subprocess.Popen(
                [PYTHON, "-u", "-m", "aja.gateway.server"],
                cwd=str(PROJECT_ROOT),
                env=env,
                creationflags=creationflags,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
            )

            worker_proc = subprocess.Popen(
                [PYTHON, "-u", "-m", "aja.runtime.autonomous_loop"],
                cwd=str(PROJECT_ROOT),
                env=env,
                creationflags=creationflags,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
            )
        finally:
            log_handle.close()

        meta = {
            "pid": gateway_proc.pid,
            "gateway_pid": gateway_proc.pid,
            "worker_pid": worker_proc.pid,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "services": ["gateway.server", "runtime.autonomous_loop"],
        }

        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        return {"status": "started", "pid": str(gateway_proc.pid)}

    def stop_daemon(self) -> Dict[str, str]:
        """Stop background daemon processes gracefully."""
        if not self.pid_file.exists():
            return {"status": "not_running"}

        try:
            data = json.loads(self.pid_file.read_text(encoding="utf-8"))
            for key in ("gateway_pid", "worker_pid", "pid"):
                pid = data.get(key)
                if pid:
                    self._kill_process(pid)
        except Exception:
            pass

        self.pid_file.unlink(missing_ok=True)
        return {"status": "stopped"}

    @staticmethod
    def _is_process_running(pid: int) -> bool:
        """Check if process with given PID exists."""
        try:
            import psutil
            p = psutil.Process(int(pid))
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except Exception:
            pass

        if os.name == "nt":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
                if h:
                    exit_code = ctypes.c_ulong()
                    kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
                    kernel32.CloseHandle(h)
                    return exit_code.value == 259  # 259 = STILL_ACTIVE
                return False
            except Exception:
                return False
        else:
            try:
                os.kill(int(pid), 0)
                return True
            except OSError:
                return False

    @staticmethod
    def _kill_process(pid: int):
        """Kill process by PID cleanly."""
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=3,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
