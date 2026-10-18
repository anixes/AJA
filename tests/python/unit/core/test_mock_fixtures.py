"""
tests/python/unit/test_mock_fixtures.py - Unit tests for centralized test fixtures and canonical mocks.
"""

import asyncio
from tests.fixtures.mocks import (
    BaseMockGateway,
    BaseMockRegistry,
    BaseMockExecutor,
    BaseFakeBot,
    BaseFakeMemory,
)


def test_base_mock_gateway_fifo_and_default():
    async def _run():
        gateway = BaseMockGateway(
            responses=["Turn 1 response", {"content": "Turn 2 response", "tool_calls": []}],
            default_response="Default fallback",
        )

        r1 = await gateway.chat(model="model-a", prompt="hello 1", system="sys 1")
        assert r1 == "Turn 1 response"
        assert gateway.turn == 1
        assert gateway.models_called == ["model-a"]
        assert gateway.last_system_prompt == "sys 1"

        r2 = await gateway.chat(model="model-b", prompt="hello 2", system="sys 2")
        assert isinstance(r2, dict)
        assert r2["content"] == "Turn 2 response"
        assert gateway.turn == 2

        # Queue exhausted, returns default
        r3 = await gateway.chat(model="model-a", prompt="hello 3")
        assert r3 == "Default fallback"
        assert gateway.turn == 3

    asyncio.run(_run())


def test_base_mock_gateway_error_injection():
    async def _run():
        class CustomRateLimit(Exception):
            pass

        gateway = BaseMockGateway(
            failure_models={"flaky-model": CustomRateLimit("429 rate limit")},
            default_response="OK",
        )

        try:
            await gateway.chat(model="flaky-model", prompt="hi")
            assert False, "Expected CustomRateLimit"
        except CustomRateLimit:
            pass

        # Other model succeeds
        res = await gateway.chat(model="good-model", prompt="hi")
        assert res == "OK"

    asyncio.run(_run())


def test_base_mock_gateway_streaming():
    async def _run():
        gateway = BaseMockGateway(default_response="Alpha Beta Gamma")
        chunks = []
        async for chunk in gateway.stream_chat(model="stream-model", prompt="stream me"):
            chunks.append(chunk["delta"]["content"])

        assert "".join(chunks) == "Alpha Beta Gamma"

    asyncio.run(_run())


def test_base_mock_registry():
    registry = BaseMockRegistry()
    assert registry.get_schemas() == []

    registry.register({"name": "test_tool", "description": "demo"})
    schemas = registry.get_schemas(interactive=False)
    assert len(schemas) == 1
    assert schemas[0]["name"] == "test_tool"
    assert len(registry.queries_seen) == 2


def test_base_mock_executor():
    async def _run():
        executor = BaseMockExecutor(default_stdout="success output")

        # Tool dispatch
        tool_calls = [{"id": "call_1", "function": {"name": "run", "arguments": "{}"}}]
        res = await executor.dispatch_tool_calls(tool_calls, trace_id="tr-123")
        assert len(res) == 1
        assert res[0].success is True
        assert len(executor.dispatched_tool_calls) == 1

        # Shell execution
        sync_res = executor.execute("echo test", cwd="/tmp")
        assert sync_res["exit_code"] == 0
        assert sync_res["stdout"] == "success output"

        async_res = await executor.execute_async("echo test async")
        assert async_res["exit_code"] == 0
        assert len(executor.executed_commands) == 2

    asyncio.run(_run())


def test_base_fake_bot_and_memory():
    async def _run():
        bot = BaseFakeBot()
        sent = await bot.send_message(chat_id=123, text="Hello User")
        assert sent["chat_id"] == 123
        assert sent["message_id"] == 101
        assert len(bot.sent_messages) == 1

        action = await bot.send_chat_action(chat_id=123, action="typing")
        assert action is True
        assert len(bot.actions_sent) == 1

        memory = BaseFakeMemory()
        await memory.set("user:123", {"tier": "pro"})
        val = await memory.get("user:123")
        assert val == {"tier": "pro"}

        doc_id = await memory.add_vector("Important project details here")
        assert doc_id == "doc_1"
        search_results = await memory.search("project")
        assert len(search_results) == 1
        assert search_results[0]["id"] == "doc_1"

    asyncio.run(_run())
