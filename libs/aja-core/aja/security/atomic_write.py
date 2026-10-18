"""
atomic_write.py - Robust, Cross-Platform Atomic File Replacement.
=================================================================

Provides safe atomic file updates using colocated temporary files,
explicit stream flushing, filesystem fsync, and exponential retry
backoff with jitter for Windows sharing violations (WinError 32/33/5).

Architecture:
- Colocated temp files: guarantees writes remain on the same filesystem/volume,
  preventing EXDEV (cross-device link) errors.
- Durability: explicit os.fsync on file descriptor before close, and parent
  directory fsync on POSIX.
- Windows Sharing Violation Defense: retries os.replace on ERROR_SHARING_VIOLATION
  (32), ERROR_LOCK_VIOLATION (33), or transient ERROR_ACCESS_DENIED (5) caused by
  antivirus scanners, indexers, or transient readers.
- Cleanup: guaranteed removal of uncommitted temporary files on failure.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import random
import time
import uuid
from typing import Union

logger = logging.getLogger(__name__)


def atomic_write(
    target_path: Union[str, Path],
    data: Union[str, bytes],
    *,
    max_retries: int = 5,
    base_backoff_s: float = 0.05,
    fsync: bool = True,
    encoding: str = "utf-8",
) -> None:
    """
    Atomically write data to target_path using a colocated temporary file
    and atomic rename (os.replace) with retry on Windows sharing violations.
    """
    target = Path(target_path).resolve()
    parent_dir = target.parent
    parent_dir.mkdir(parents=True, exist_ok=True)

    # Encode string data if bytes were not provided
    payload = data.encode(encoding) if isinstance(data, str) else data

    # Colocated temporary file on the same volume
    tmp_name = f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    tmp_path = parent_dir / tmp_name

    try:
        # 1. Write content to temp file with explicit fsync
        with open(tmp_path, "wb") as f:
            f.write(payload)
            if fsync:
                f.flush()
                os.fsync(f.fileno())

        # 2. Atomic replace with Windows sharing violation retry
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                os.replace(str(tmp_path), str(target))
                last_error = None
                break
            except OSError as e:
                last_error = e
                win_err = getattr(e, "winerror", None)

                # Retry on Windows sharing violation (32), lock violation (33),
                # or transient access denied (5)
                is_transient = win_err in (32, 33, 5) or (
                    os.name == "nt" and e.errno == 13
                )

                if is_transient and attempt < max_retries - 1:
                    sleep_time = (base_backoff_s * (2**attempt)) + random.uniform(
                        0.0, 0.03
                    )
                    time.sleep(sleep_time)
                    continue
                raise

        if last_error is not None:
            raise RuntimeError(
                f"Failed to atomically replace '{target}' after {max_retries} attempts"
            ) from last_error

        # 3. On POSIX, fsync parent directory descriptor for metadata durability
        if fsync and os.name == "posix":
            try:
                dir_fd = os.open(str(parent_dir), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as e:
                logger.debug("Directory fsync failed or unsupported: %s", e)

    except Exception:
        # Guarantee cleanup of uncommitted temporary file
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
