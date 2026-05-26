import pytest
from aja.runtime.execution.activity import set_activity_context

@pytest.fixture(autouse=True)
def clear_activity_context():
    # Clear context before test runs
    set_activity_context(None)
    yield
    # Clear context after test runs
    set_activity_context(None)
