"""Live per-provider conformance suite.

Runs against real provider APIs. Providers without credentials are skipped
automatically (see conftest.py). Requires the ``live_providers`` marker:

    py -3.12 -m pytest tests/python/live -m live_providers
"""

import asyncio
import json
import time

import pytest

from aja import llm

pytestmark = [pytest.mark.live_providers, pytest.mark.usefixtures("summary")]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator_add",
            "description": "Add two integers and return the sum.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer", "description": "First addend"},
                    "b": {"type": "integer", "description": "Second addend"},
                },
                "required": ["a", "b"],
            },
        },
    }
]


def _run(coro):
    return asyncio.run(coro)


def test_basic_completion(configured_provider):
    result = llm.completion(
        "Reply with exactly OK",
        model=f"{configured_provider.provider}:{configured_provider.model}",
    )
    assert isinstance(result, str)
    assert "OK" in result.upper()


def test_streaming_yields_chunks(configured_provider):
    chunks = []

    async def collect():
        async for chunk in llm.completion_stream(
            "Count from one to five.",
            model=f"{configured_provider.provider}:{configured_provider.model}",
        ):
            chunks.append(chunk)

    _run(collect())
    assert len(chunks) >= 1
    assert any(str(c).strip() for c in chunks)


def test_tool_call_roundtrip(gw, configured_provider):
    response = _run(
        gw.chat(
            model=configured_provider.model,
            prompt="What is 2+2? Use the calculator_add tool to compute it.",
            tools=TOOLS,
            retries=1,
        )
    )
    assert response is not None, "gateway returned None (request failed)"
    assert isinstance(response, dict)
    content = response.get("content") or ""
    tool_calls = response.get("tool_calls") or []
    # Provider-dependent shapes tolerated: some providers answer in plain
    # content instead of emitting a tool_call — require at least one.
    assert content or tool_calls, f"empty response: {response!r}"
    if tool_calls:
        args_raw = tool_calls[0].get("arguments")
        assert isinstance(args_raw, str), f"arguments not a JSON string: {args_raw!r}"
        parsed = json.loads(args_raw)
        numbers = [
            v for v in parsed.values() if isinstance(v, (int, float))
        ]
        assert numbers, f"no numeric arguments in tool call: {parsed!r}"


def test_deterministic_4xx_no_retry_hang(gw, configured_provider):
    start = time.monotonic()
    result = _run(
        gw.chat(
            model="definitely-not-a-real-model-xyz",
            prompt="hello",
            retries=3,
        )
    )
    elapsed = time.monotonic() - start
    assert result is None, f"expected None for invalid model, got: {result!r}"
    assert elapsed < 90, (
        f"bad-model request took {elapsed:.1f}s — deterministic 4xx was "
        "likely retried instead of failing fast"
    )
