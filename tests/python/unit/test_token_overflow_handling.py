"""Unit tests for token overflow and rate limit handling (Task 2.2 / H2).

Verifies:
1. atomic_prune_messages preserves system + user task with preserve_first=2.
2. 429 TPM/RPM rate limits back off without pruning history.
3. Context length overflows prune older turns while preserving initial goal.
4. Dedicated overflow retries counter allows retry even when retries=1.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from aja.orchestration.context_window import atomic_prune_messages
from aja.orchestration.providers.openai_compat import OpenAICompatAdapter


def test_atomic_prune_messages_preserve_first_two():
    messages = [
        {"role": "system", "content": "System instructions"},
        {"role": "user", "content": "User original objective"},
        {"role": "assistant", "content": "Step 1", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "res1"},
        {"role": "assistant", "content": "Step 2", "tool_calls": [{"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c2", "content": "res2"},
        {"role": "assistant", "content": "Final output"},
    ]

    dropped = atomic_prune_messages(messages, target_drops=1, preserve_first=2)
    assert dropped >= 2
    assert messages[0] == {"role": "system", "content": "System instructions"}
    assert messages[1] == {"role": "user", "content": "User original objective"}
    assert messages[2]["content"] == "Step 2"
    assert messages[3]["content"] == "res2"


@pytest.mark.anyio
async def test_openai_compat_token_overflow_retries_with_single_attempt():
    provider = OpenAICompatAdapter(provider="openai", api_key="sk-test", base_url="http://fake")

    call_records = []

    async def fake_create(**kwargs):
        call_records.append([dict(m) for m in kwargs["messages"]])
        if len(call_records) == 1:
            err = Exception("Error code 400: context_length_exceeded (model maximum is 8192 tokens)")
            err.status_code = 400
            raise err
        msg = SimpleNamespace(content="Resolved after pruning", tool_calls=None)
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice], usage=None)

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    history = [
        {"role": "user", "content": "Primary goal"},
        {"role": "assistant", "content": "Old turn 1"},
        {"role": "user", "content": "Old turn 2"},
        {"role": "assistant", "content": "Recent turn 3"},
    ]

    with patch.object(provider, "_get_client", return_value=fake_client):
        # Even with retries=1, dedicated overflow retries counter must trigger
        resp = await provider.chat(
            model="gpt-4",
            messages=history,
            system="System directive",
            retries=1,
        )

    assert resp.content == "Resolved after pruning"
    assert len(call_records) == 2
    first_call_msgs = call_records[0]
    second_call_msgs = call_records[1]
    assert len(second_call_msgs) < len(first_call_msgs)
    # Both system directive and user primary goal are preserved
    assert second_call_msgs[0] == {"role": "system", "content": "System directive"}
    assert second_call_msgs[1] == {"role": "user", "content": "Primary goal"}


@pytest.mark.anyio
async def test_openai_compat_rate_limit_backs_off_without_pruning():
    provider = OpenAICompatAdapter(provider="openai", api_key="sk-test", base_url="http://fake")

    call_records = []

    async def fake_create(**kwargs):
        call_records.append([dict(m) for m in kwargs["messages"]])
        if len(call_records) == 1:
            err = Exception("Rate limit reached for requests per minute (RPM) or tokens per minute (TPM)")
            err.status_code = 429
            raise err
        msg = SimpleNamespace(content="Resolved after backoff", tool_calls=None)
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice], usage=None)

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    history = [
        {"role": "user", "content": "Primary goal"},
        {"role": "assistant", "content": "Old turn 1"},
        {"role": "user", "content": "Old turn 2"},
    ]

    with patch.object(provider, "_get_client", return_value=fake_client), patch(
        "aja.orchestration.providers.openai_compat._backoff_sleep", new_callable=AsyncMock
    ) as mock_sleep:
        resp = await provider.chat(
            model="gpt-4",
            messages=history,
            system="System directive",
            retries=2,
        )

    assert resp.content == "Resolved after backoff"
    assert len(call_records) == 2
    # Crucial assertion: 429 rate limit must NOT prune messages
    assert len(call_records[1]) == len(call_records[0])
    assert mock_sleep.called


@pytest.mark.anyio
async def test_gateway_chat_rate_limit_does_not_prune():
    from aja.orchestration.gateway import LLMGateway

    gw = LLMGateway(provider="openai", api_key="sk-test", base_url="http://fake")
    call_records = []

    async def fake_create(**kwargs):
        call_records.append([dict(m) for m in kwargs["messages"]])
        if len(call_records) == 1:
            err = Exception("Rate limit exceeded: 429 Too Many Requests (TPM)")
            err.status_code = 429
            raise err
        msg = SimpleNamespace(content="Gateway reply", tool_calls=None)
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    prompt = [
        {"role": "user", "content": "Goal"},
        {"role": "assistant", "content": "Turn 1"},
        {"role": "user", "content": "Turn 2"},
    ]

    with patch.object(gw, "_get_openai_client", return_value=fake_client), patch(
        "aja.orchestration.gateway._backoff_sleep", new_callable=AsyncMock
    ):
        result = await gw.chat(model="gpt-4", prompt=prompt, retries=2)

    assert result == "Gateway reply"
    assert len(call_records) == 2
    # No pruning on rate limit
    assert len(call_records[0]) == len(call_records[1])
