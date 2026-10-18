"""
test_atomic_write.py - Unit Tests for Atomic File Replacement.
==============================================================
Validates:
- Basic atomic write and overwrite semantics.
- Stream flushing and filesystem fsync.
- Simulated Windows sharing violation (WinError 32) retry with backoff.
- Temporary file cleanup upon permanent error.
- Unicode and byte payload handling.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from aja.security.atomic_write import atomic_write


def test_atomic_write_basic_and_overwrite(tmp_path: Path):
    """Verify writing new file and subsequently overwriting it atomically."""
    target_file = tmp_path / "test_file.txt"

    # 1. Initial write
    atomic_write(target_file, "Initial content payload")
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "Initial content payload"

    # Verify no dangling temp files in parent
    tmp_files = list(tmp_path.glob(".*.tmp"))
    assert len(tmp_files) == 0

    # 2. Overwrite
    atomic_write(target_file, "Updated replacement content")
    assert target_file.read_text(encoding="utf-8") == "Updated replacement content"
    assert len(list(tmp_path.glob(".*.tmp"))) == 0


def test_atomic_write_bytes_and_unicode(tmp_path: Path):
    """Verify binary bytes and multi-byte UTF-8 data writes."""
    target_bin = tmp_path / "data.bin"
    raw_bytes = b"\x00\xff\xfe\x01\x02\x03\xaa\xbb"
    atomic_write(target_bin, raw_bytes)
    assert target_bin.read_bytes() == raw_bytes

    target_unicode = tmp_path / "unicode.txt"
    unicode_text = "Universal Agent: 🤖 🚀 日本語, Français, Español"
    atomic_write(target_unicode, unicode_text)
    assert target_unicode.read_text(encoding="utf-8") == unicode_text


def test_atomic_write_transient_sharing_violation_retry(tmp_path: Path):
    """Verify exponential backoff retry when Windows returns WinError 32 sharing violation."""
    target_file = tmp_path / "retry_file.txt"
    real_replace = os.replace
    attempts = 0

    def mock_replace(src, dst):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            # Simulate Windows WinError 32: ERROR_SHARING_VIOLATION
            err = OSError("The process cannot access the file because it is being used by another process.")
            err.winerror = 32
            raise err
        return real_replace(src, dst)

    with patch("os.replace", side_effect=mock_replace):
        atomic_write(target_file, "Content after sharing violation retries")

    assert attempts == 3
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "Content after sharing violation retries"
    assert len(list(tmp_path.glob(".*.tmp"))) == 0


def test_atomic_write_permanent_failure_cleans_up_tmp(tmp_path: Path):
    """Verify that permanent replace failure cleans up temporary file and raises RuntimeError."""
    target_file = tmp_path / "failing_file.txt"

    def mock_failing_replace(src, dst):
        err = OSError("Permanent filesystem error")
        err.winerror = 999  # Non-retryable
        raise err

    with patch("os.replace", side_effect=mock_failing_replace):
        with pytest.raises(OSError):
            atomic_write(target_file, "Doomed content")

    # Target must not exist
    assert not target_file.exists()
    # Temp file must have been cleaned up
    tmp_files = list(tmp_path.glob(".*.tmp"))
    assert len(tmp_files) == 0
