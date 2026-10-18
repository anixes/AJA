"""
tests/python/unit/test_client_factory.py - Unit tests for shared client_factory kernel.
"""

import asyncio
from pathlib import Path
import pytest

from aja.gateway.client_factory import (
    backoff_sleep_seconds,
    backoff_sleep,
    llm_timeout_s,
    find_project_root,
    get_async_openai_client,
    clear_client_cache,
)


def test_backoff_sleep_seconds_bounds():
    for attempt in range(6):
        delay = backoff_sleep_seconds(attempt, max_delay=30.0)
        expected_max = min(30.0, 2.0 ** attempt)
        expected_min = expected_max * 0.5
        assert expected_min <= delay <= expected_max


def test_backoff_sleep_seconds_max_cap():
    delay = backoff_sleep_seconds(10, max_delay=15.0)
    assert 7.5 <= delay <= 15.0


def test_backoff_sleep_async():
    async def _run():
        # Test backoff_sleep with 0 delay doesn't crash
        await backoff_sleep(0, max_delay=0.01)

    asyncio.run(_run())


def test_llm_timeout_s_default(monkeypatch):
    monkeypatch.delenv("AJA_LLM_TIMEOUT_S", raising=False)
    assert llm_timeout_s() == 120.0

    monkeypatch.setenv("AJA_LLM_TIMEOUT_S", "45.5")
    assert llm_timeout_s() == 45.5

    monkeypatch.setenv("AJA_LLM_TIMEOUT_S", "invalid")
    assert llm_timeout_s() == 120.0


def test_find_project_root():
    root = find_project_root()
    assert isinstance(root, Path)
    assert root.exists()


def test_get_async_openai_client_cached():
    async def _run():
        clear_client_cache()
        client1 = get_async_openai_client("https://api.example.com/v1", "key-123")
        client2 = get_async_openai_client("https://api.example.com/v1", "key-123")
        assert client1 is client2

        # Different key produces different client
        client3 = get_async_openai_client("https://api.example.com/v1", "key-456")
        assert client1 is not client3

        clear_client_cache()

    asyncio.run(_run())
