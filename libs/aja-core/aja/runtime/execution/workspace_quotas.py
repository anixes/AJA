"""
workspace_quotas.py — Resource quotas, sandboxing guards, and usage trackers for AJA missions.
=============================================================================================
Enforces disk-write quotas, subprocess count ceilings, execution timeouts, and workspace
filesystem confinement across direct loop tool dispatches. Strictly model-agnostic.
"""

from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, Union

from aja.config_schema import WorkspaceQuotas


class QuotaExceededError(RuntimeError):
    """Base exception raised when a mission resource quota is exceeded."""
    pass


class DiskQuotaExceededError(QuotaExceededError):
    """Raised when single-file or cumulative write byte limits are exceeded."""
    def __init__(self, attempted_bytes: int, current_usage: int, limit: int, is_single_file: bool = False):
        self.attempted_bytes = attempted_bytes
        self.current_usage = current_usage
        self.limit = limit
        self.is_single_file = is_single_file
        if is_single_file:
            msg = (
                f"Single file write quota exceeded: attempted {attempted_bytes} bytes "
                f"(maximum allowed: {limit} bytes)."
            )
        else:
            msg = (
                f"Cumulative disk write quota exceeded: attempted {attempted_bytes} bytes "
                f"(current usage: {current_usage} bytes, limit: {limit} bytes)."
            )
        super().__init__(msg)


class SubprocessQuotaExceededError(QuotaExceededError):
    """Raised when the maximum number of command/subprocess executions is exceeded."""
    def __init__(self, current_count: int, limit: int):
        self.current_count = current_count
        self.limit = limit
        super().__init__(
            f"Subprocess execution quota exceeded: attempted {current_count} calls "
            f"(maximum allowed: {limit} calls)."
        )


class TimeQuotaExceededError(QuotaExceededError):
    """Raised when the mission wall-clock execution time exceeds the configured ceiling."""
    def __init__(self, elapsed_seconds: float, limit: float):
        self.elapsed_seconds = elapsed_seconds
        self.limit = limit
        super().__init__(
            f"Mission wall-clock execution time exceeded: elapsed {elapsed_seconds:.2f}s "
            f"(maximum allowed: {limit:.2f}s)."
        )


class SandboxViolationError(PermissionError):
    """Raised when a tool operation attempts to access or modify a path outside the workspace."""
    def __init__(self, path: Union[Path, str], workspace_root: Union[Path, str]):
        self.path = str(path)
        self.workspace_root = str(workspace_root)
        super().__init__(
            f"Sandbox confinement violation: target path '{self.path}' escapes "
            f"workspace root '{self.workspace_root}'."
        )


@dataclass
class QuotaSnapshot:
    """Immutable snapshot of current quota consumption."""
    disk_bytes_used: int
    disk_bytes_limit: int
    single_file_limit: int
    subprocess_calls_made: int
    subprocess_calls_limit: int
    start_time: float
    current_time: float
    tool_calls_count: int

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self.current_time - self.start_time)


def verify_path_confinement(
    path: Union[Path, str],
    workspace_root: Union[Path, str],
    allow_out_of_bounds: bool = False,
) -> Path:
    """Verify that target `path` resolves strictly within `workspace_root`.

    Returns the canonical Path if valid. Raises `SandboxViolationError` if the
    path escapes and `allow_out_of_bounds` is False.
    """
    if allow_out_of_bounds:
        return Path(path).resolve()

    root_path = Path(workspace_root).resolve()
    # If path is relative, anchor it to root_path before resolving
    target_p = Path(path)
    if not target_p.is_absolute():
        resolved_path = (root_path / target_p).resolve()
    else:
        resolved_path = target_p.resolve()

    try:
        norm_resolved = Path(os.path.normcase(str(resolved_path)))
        norm_root = Path(os.path.normcase(str(root_path)))
        if hasattr(norm_resolved, "is_relative_to"):
            contained = norm_resolved.is_relative_to(norm_root)
        else:
            norm_resolved.relative_to(norm_root)
            contained = True
    except (ValueError, Exception):
        contained = False

    if not contained:
        raise SandboxViolationError(path, workspace_root)

    return resolved_path


class QuotaTracker:
    """Thread-safe and async-safe runtime resource tracker and quota guard."""

    def __init__(
        self,
        quotas: Optional[WorkspaceQuotas] = None,
        workspace_root: Optional[Union[Path, str]] = None,
    ):
        self.quotas = quotas or WorkspaceQuotas()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._lock = threading.Lock()

        self._disk_bytes_used = 0
        self._subprocess_calls = 0
        self._tool_calls_count = 0
        self._start_time = time.monotonic()

    def check_and_reserve_write(
        self,
        size_bytes: int,
        target_path: Optional[Union[Path, str]] = None,
    ) -> None:
        """Verify that a write operation conforms to single-file and cumulative quotas,
        and optionally verifies workspace confinement.
        """
        if target_path and self.workspace_root:
            verify_path_confinement(
                target_path,
                self.workspace_root,
                allow_out_of_bounds=self.quotas.allow_out_of_bounds_paths,
            )

        with self._lock:
            # 1. Single-file quota check
            if size_bytes > self.quotas.max_single_file_write_bytes:
                raise DiskQuotaExceededError(
                    attempted_bytes=size_bytes,
                    current_usage=self._disk_bytes_used,
                    limit=self.quotas.max_single_file_write_bytes,
                    is_single_file=True,
                )

            # 2. Cumulative disk quota check
            if self._disk_bytes_used + size_bytes > self.quotas.max_cumulative_write_bytes:
                raise DiskQuotaExceededError(
                    attempted_bytes=size_bytes,
                    current_usage=self._disk_bytes_used,
                    limit=self.quotas.max_cumulative_write_bytes,
                    is_single_file=False,
                )

            # Reserve bytes atomically
            self._disk_bytes_used += size_bytes

    def record_subprocess_call(self) -> None:
        """Increment subprocess execution count and enforce maximum calls limit."""
        with self._lock:
            self._subprocess_calls += 1
            if self._subprocess_calls > self.quotas.max_subprocess_calls:
                raise SubprocessQuotaExceededError(
                    current_count=self._subprocess_calls,
                    limit=self.quotas.max_subprocess_calls,
                )

    def check_time_limit(self) -> None:
        """Verify that mission wall-clock execution has not exceeded the maximum duration."""
        with self._lock:
            elapsed = time.monotonic() - self._start_time
            if elapsed > self.quotas.max_total_execution_seconds:
                raise TimeQuotaExceededError(
                    elapsed_seconds=elapsed,
                    limit=self.quotas.max_total_execution_seconds,
                )

    def record_tool_call(self) -> None:
        """Increment overall dispatched tool calls counter."""
        with self._lock:
            self._tool_calls_count += 1

    def get_snapshot(self) -> QuotaSnapshot:
        with self._lock:
            return QuotaSnapshot(
                disk_bytes_used=self._disk_bytes_used,
                disk_bytes_limit=self.quotas.max_cumulative_write_bytes,
                single_file_limit=self.quotas.max_single_file_write_bytes,
                subprocess_calls_made=self._subprocess_calls,
                subprocess_calls_limit=self.quotas.max_subprocess_calls,
                start_time=self._start_time,
                current_time=time.monotonic(),
                tool_calls_count=self._tool_calls_count,
            )

    def get_attribution_data(self) -> Dict[str, Any]:
        snap = self.get_snapshot()
        return {
            "bytes_written": snap.disk_bytes_used,
            "subprocess_calls": snap.subprocess_calls_made,
            "total_execution_seconds": snap.elapsed_seconds,
            "tool_calls_count": snap.tool_calls_count,
        }
