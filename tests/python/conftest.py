import os
import asyncio
import logging
import sys
import tempfile
from pathlib import Path

import pytest

# Global performance flags for test suite speedup
os.environ["AJA_MOCK_EMBEDDINGS"] = "1"
os.environ["AJA_FAST_SANDBOX"] = "1"
os.environ["GOOGLE_API_KEY"] = "test-mock-key"

# ---------------------------------------------------------------------------
# Test run data isolation (both serial and pytest-xdist).
#
# MUST run before any `aja` import: aja.config reads AJA_DATA_DIR at import
# time. Each test runner process gets its own throwaway data directory so LanceDB
# tables, mission journals, daemon PID files, and goal-engine state files are
# never shared with production or between concurrently executing tests.
# ---------------------------------------------------------------------------
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER") or os.environ.get("_PYTEST_XDIST_WORKER")
_worker_label = f"xdist_{_XDIST_WORKER}" if _XDIST_WORKER else "serial"
_isolated_root = Path(tempfile.gettempdir()) / f"aja_test_{_worker_label}_{os.getpid()}"
_isolated_data = _isolated_root / "data"
_isolated_data.mkdir(parents=True, exist_ok=True)
if "AJA_DATA_DIR" not in os.environ:
    os.environ["AJA_DATA_DIR"] = str(_isolated_data)
if "AJA_TRACE_DIR" not in os.environ:
    os.environ["AJA_TRACE_DIR"] = str(_isolated_root / "traces")
os.environ.setdefault("AJA_TEST_MODE", "1")

if sys.platform == "win32":
    # ProactorEventLoop is required on Windows for async subprocess support.
    # SelectorEventLoop (the old default before Python 3.8) does not support
    # subprocess transports, which causes asyncio.create_subprocess_exec() and
    # anyio shell activities to fail silently or raise NotImplementedError.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logger = logging.getLogger(__name__)


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.fixture(autouse=True)
def clear_activity_context():
    try:
        from aja.runtime.execution.activity import set_activity_context
    except ImportError as e:
        logger.warning(f"Skipping set_activity_context in conftest.py: {e}")
        yield
        return

    # Clear context before test runs
    set_activity_context(None)
    yield
    # Clear context after test runs
    set_activity_context(None)


# ---------------------------------------------------------------------------
# Memory-leak containment (see docs/plans/MEMORY_LEAK_FINDINGS.md).
#
# Long test sessions ratcheted a single worker process to 18-22GB virtual:
# cached LLMGateways pin event loops + connection pools, the global event bus
# accumulates subscribers, and experience_store retains whole plan/result
# object graphs. Cheap in-memory resets run after EVERY test; LanceDB-backed
# singletons (secretary/manager) reset SESSION-scoped only — per-test resets
# there forced expensive re-init disk IO that hung planning tests.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_leaky_singletons():
    yield
    # Each step individually guarded: a missing/renamed hook must never fail
    # the suite — this fixture is hygiene, not behavior.
    try:
        from aja.llm import clear_gateway_cache

        clear_gateway_cache()
    except Exception:
        pass
    try:
        from aja.runtime.event_bus import bus

        bus.reset()
    except Exception:
        pass
    try:
        from aja.memory.experience_store import experience_store

        experience_store.store.clear()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _reset_lancedb_singletons_session():
    yield
    try:
        from aja.memory import secretary as _secretary

        _secretary._instance = None
    except Exception:
        pass
    try:
        from aja.memory import manager as _memmgr

        _memmgr._instance = None
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Canonical Test Mock Fixtures (tests.fixtures.mocks)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_gateway():
    """Provides a fresh BaseMockGateway instance for unit testing."""
    from tests.fixtures.mocks import BaseMockGateway
    return BaseMockGateway()


@pytest.fixture
def mock_registry():
    """Provides a fresh BaseMockRegistry instance for unit testing."""
    from tests.fixtures.mocks import BaseMockRegistry
    return BaseMockRegistry()


@pytest.fixture
def mock_executor():
    """Provides a fresh BaseMockExecutor instance for unit testing."""
    from tests.fixtures.mocks import BaseMockExecutor
    return BaseMockExecutor()


@pytest.fixture
def fake_bot():
    """Provides an in-memory BaseFakeBot instance for chat interface testing."""
    from tests.fixtures.mocks import BaseFakeBot
    return BaseFakeBot()


@pytest.fixture
def fake_memory():
    """Provides an in-memory BaseFakeMemory instance for vector/KV memory testing."""
    from tests.fixtures.mocks import BaseFakeMemory
    return BaseFakeMemory()
