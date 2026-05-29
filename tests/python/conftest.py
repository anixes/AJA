import pytest
import logging
import sys
import asyncio

if sys.platform == "win32":
    # We will use the default ProactorEventLoop since SelectorEventLoop doesn't support subprocesses
    pass

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
