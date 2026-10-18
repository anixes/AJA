"""
tests/conftest.py - Root test configuration and canonical fixture forwarder.
"""

from tests.fixtures.mocks import (
    BaseMockGateway,
    BaseMockRegistry,
    BaseMockExecutor,
    BaseFakeBot,
    BaseFakeMemory,
)
import pytest


@pytest.fixture
def mock_gateway():
    return BaseMockGateway()


@pytest.fixture
def mock_registry():
    return BaseMockRegistry()


@pytest.fixture
def mock_executor():
    return BaseMockExecutor()


@pytest.fixture
def fake_bot():
    return BaseFakeBot()


@pytest.fixture
def fake_memory():
    return BaseFakeMemory()
