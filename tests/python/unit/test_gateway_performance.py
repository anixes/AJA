"""
=============================================================================
Unit Test: Gateway Caching, Loop-Aware Session Pooling, and Streaming
=============================================================================
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aja.orchestration.gateway import LLMGateway
from aja.llm import get_gateway_for_model, clear_gateway_cache, completion_stream, completion_async


def test_gateway_instance_caching():
    clear_gateway_cache()
    gw1, model1 = get_gateway_for_model("google:gemini-2.5-flash")
    gw2, model2 = get_gateway_for_model("google:gemini-2.5-flash")

    assert gw1 is gw2  # Must be the exact same cached instance in memory!
    assert model1 == "gemini-2.5-flash"


def test_gateway_session_pooling_reuse():
    async def _test():
        gw = LLMGateway(provider="openrouter", api_key="test-key", base_url="https://openrouter.ai/api/v1")

        # Call _get_session twice in the same event loop
        session1 = gw._get_session()
        session2 = gw._get_session()

        assert session1 is session2  # Connection pool / session is preserved and reused!
        assert not session1.closed

        await gw.close()
        assert session1.closed

    asyncio.run(_test())


def test_completion_stream_generator():
    async def _test():
        with patch("aja.llm.get_gateway_for_model") as mock_get_gw:
            mock_gw = MagicMock()

            async def fake_stream(*args, **kwargs):
                yield "Hello "
                yield "Operator! "
                yield "Ready."

            mock_gw.chat_stream = fake_stream
            mock_get_gw.return_value = (mock_gw, "gemini-2.5-flash")

            chunks = []
            async for chunk in completion_stream("Hi"):
                chunks.append(chunk)

            assert "".join(chunks) == "Hello Operator! Ready."

    asyncio.run(_test())
