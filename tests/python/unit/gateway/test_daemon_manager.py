"""
Unit tests for DaemonManager (Process Supervisor)
"""

import json
from pathlib import Path
from aja.gateway.daemon_manager import DaemonManager


def test_daemon_manager_initial_status_stopped(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    log_file = tmp_path / "daemon.log"

    mgr = DaemonManager(pid_file=pid_file, log_file=log_file)
    status = mgr.get_status()

    assert status["status"] == "stopped"
    assert status["pid"] is None


def test_daemon_manager_stale_pid_cleanup(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    log_file = tmp_path / "daemon.log"

    # Write fake PID that doesn't exist
    stale_meta = {
        "pid": 999999,
        "started_at": "2026-01-01 00:00:00",
        "services": ["test.service"],
    }
    pid_file.write_text(json.dumps(stale_meta), encoding="utf-8")

    mgr = DaemonManager(pid_file=pid_file, log_file=log_file)
    status = mgr.get_status()

    assert status["status"] == "stopped"
    assert not pid_file.exists()
