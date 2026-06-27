import asyncio
import logging
import sys

import pytest

if sys.platform == "win32":
    # ProactorEventLoop is required on Windows for async subprocess support.
    # SelectorEventLoop (the old default before Python 3.8) does not support
    # subprocess transports, which causes asyncio.create_subprocess_exec() and
    # anyio shell activities to fail silently or raise NotImplementedError.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logger = logging.getLogger(__name__)


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
