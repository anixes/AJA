"""
Unit tests for WorkspaceLock (P0.1) in aja.runtime.workspace_lock.
"""

import json
import os
import sys
from pathlib import Path
import pytest

from aja.runtime.workspace_lock import WorkspaceLock, WorkspaceLockError, is_pid_alive


def test_is_pid_alive_current_and_dead():
    current_pid = os.getpid()
    assert is_pid_alive(current_pid) is True
    # Non-existent PID
    assert is_pid_alive(99999999) is False
    assert is_pid_alive(-1) is False
    assert is_pid_alive(0) is False


def test_workspace_lock_acquire_and_release(tmp_path: Path):
    lock = WorkspaceLock(workspace_root=tmp_path, objective="Test Run")
    assert lock.acquired is False
    assert not (tmp_path / ".aja" / "workspace.lock").exists()

    lock.acquire()
    assert lock.acquired is True
    lock_file = tmp_path / ".aja" / "workspace.lock"
    assert lock_file.exists()

    data = lock.read_lock_info()
    assert data is not None
    assert data["pid"] == os.getpid()
    assert data["objective"] == "Test Run"
    assert "created_at" in data
    assert "hostname" in data

    lock.release()

    assert lock.acquired is False
    assert not lock_file.exists()


def test_workspace_lock_context_manager(tmp_path: Path):
    lock_file = tmp_path / ".aja" / "workspace.lock"
    with WorkspaceLock(workspace_root=tmp_path, objective="Context test"):
        assert lock_file.exists()
    assert not lock_file.exists()


def test_workspace_lock_collision_with_live_process(tmp_path: Path):
    lock1 = WorkspaceLock(workspace_root=tmp_path, objective="First")
    lock1.acquire()

    lock2 = WorkspaceLock(workspace_root=tmp_path, objective="Second")
    with pytest.raises(WorkspaceLockError) as exc_info:
        lock2.acquire()
    assert "locked by another running AJA instance" in str(exc_info.value) or "OS lock" in str(exc_info.value)

    lock1.release()
    # Now lock2 can acquire
    lock2.acquire()
    assert lock2.acquired is True
    lock2.release()


def test_workspace_lock_stale_lock_recovery(tmp_path: Path):
    lock_dir = tmp_path / ".aja"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "workspace.lock"

    # Simulate stale lock from a dead PID (99999999)
    stale_metadata = {
        "pid": 99999999,
        "hostname": "old-ghost-host",
        "created_at": "2020-01-01T00:00:00Z",
        "objective": "Zombie task",
    }
    lock_file.write_text(json.dumps(stale_metadata), encoding="utf-8")

    # New lock should automatically detect that PID 99999999 is dead and acquire
    lock = WorkspaceLock(workspace_root=tmp_path, objective="Fresh run")
    lock.acquire()
    assert lock.acquired is True

    # Lockfile should now reflect current PID
    new_data = lock.read_lock_info()
    assert new_data is not None
    assert new_data["pid"] == os.getpid()
    assert new_data["objective"] == "Fresh run"

    lock.release()


def test_workspace_lock_force_override(tmp_path: Path):
    lock_dir = tmp_path / ".aja"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "workspace.lock"

    stale_metadata = {
        "pid": os.getpid(),  # simulate same pid or existing
        "hostname": "local",
        "created_at": "2026-10-18T00:00:00Z",
        "objective": "Existing",
    }
    lock_file.write_text(json.dumps(stale_metadata), encoding="utf-8")

    lock = WorkspaceLock(workspace_root=tmp_path, objective="Force run", force=True)
    lock.acquire()
    assert lock.acquired is True
    lock.release()
