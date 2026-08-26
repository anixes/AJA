"""Session-scoped memory guard for the test suite.

Logs python.exe working-set at session start/end and warns after any test
that pushes RSS past a threshold, so RAM-pressure crashes (16GB host,
flaky RAM stick) can be attributed to a specific test.

Enable verbose per-test reporting with AJA_MEM_GUARD=1. Threshold is
AJA_MEM_GUARD_MAX_MB (default 4096).

Registered via pyproject.toml addopts: `-p tests.python.memguard_plugin`.
"""

import logging
import os

import pytest

logger = logging.getLogger("aja.memguard")

_THRESHOLD_MB = float(os.environ.get("AJA_MEM_GUARD_MAX_MB", "4096"))
_VERBOSE = os.environ.get("AJA_MEM_GUARD") == "1"


def _rss_mb() -> float:
    try:
        import ctypes
        import ctypes.wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        # K32GetProcessMemoryInfo (kernel32) works reliably; the psapi alias
        # fails depending on process token/HANDLE typing (err 6).
        k32 = ctypes.windll.kernel32.K32GetProcessMemoryInfo
        k32.restype = ctypes.wintypes.BOOL
        if k32(
            ctypes.wintypes.HANDLE(ctypes.windll.kernel32.GetCurrentProcess()),
            ctypes.byref(pmc),
            pmc.cb,
        ):
            return pmc.WorkingSetSize / (1024 * 1024)
        return -1.0
    except Exception:
        return -1.0


def pytest_sessionstart(session):
    logger.info("[memguard] session start rss=%.0fMB", _rss_mb())


def pytest_runtest_teardown(item, nextitem):
    mb = _rss_mb()
    if mb > _THRESHOLD_MB:
        logger.warning(
            "[memguard] HIGH MEMORY after %s: rss=%.0fMB (threshold %.0fMB) "
            "- candidate leak; run this test in isolation to confirm",
            item.nodeid,
            mb,
            _THRESHOLD_MB,
        )


def pytest_sessionfinish(session, exitstatus):
    logger.info("[memguard] session finish end=%.0fMB", _rss_mb())
