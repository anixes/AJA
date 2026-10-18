"""
tests/python/unit/test_gbnf.py
==============================
Unit tests for GBNF grammar generation, JSON schema compilation, and
structured tool calling constraints for llama.cpp.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aja.models.gbnf import (
    build_tool_call_grammar,
    parse_constrained_tool_response,
    tools_to_json_schema,
)
from aja.orchestration.providers.base import ToolCall
from aja.orchestration.providers.openai_compat import OpenAICompatAdapter


SAMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run shell command",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file from disk",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
]


def test_tools_to_json_schema_structure():
    """Verify that tools are compiled into an anyOf JSON schema with individual parameter validation."""
    schema = tools_to_json_schema(SAMPLE_TOOLS, allow_text_reply=True)

    assert schema["type"] == "object"
    assert "properties" in schema
    assert "action" in schema["properties"]
    assert schema["properties"]["action"]["enum"] == ["tool_call", "message"]

    tool_call_prop = schema["properties"]["tool_call"]
    assert "anyOf" in tool_call_prop
    assert len(tool_call_prop["anyOf"]) == 2

    tool_names = [branch["properties"]["name"]["const"] for branch in tool_call_prop["anyOf"]]
    assert "bash" in tool_names
    assert "read_file" in tool_names


def test_build_tool_call_grammar_rules():
    """Verify generated GBNF grammar syntax and tool name alternatives."""
    grammar = build_tool_call_grammar(SAMPLE_TOOLS, allow_narrative=True)

    assert "root ::= tool-call | text-message" in grammar
    assert 'func-name ::= "bash" | "read_file"' in grammar
    assert "json-object" in grammar
    assert "json-pair" in grammar
    assert "string" in grammar


def test_build_tool_call_grammar_empty_tools():
    """Empty tool list gracefully generates open text rule."""
    grammar = build_tool_call_grammar([])
    assert "root ::= [^\\x00]+" in grammar


def test_parse_constrained_tool_response_action_format():
    """Verify parsing of standard structured action tool call."""
    payload = {
        "thought": "I need to inspect the directory contents.",
        "action": "tool_call",
        "tool_call": {
            "name": "bash",
            "arguments": {"command": "dir"},
        },
    }
    content = json.dumps(payload)
    thought, tool_calls = parse_constrained_tool_response(content)

    assert thought == "I need to inspect the directory contents."
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "bash"
    assert json.loads(tool_calls[0]["arguments"]) == {"command": "dir"}


def test_parse_constrained_tool_response_action_message():
    """Verify parsing when model chooses direct text message action."""
    payload = {
        "thought": "No tool needed.",
        "action": "message",
        "message": "Task complete with zero errors.",
    }
    content = json.dumps(payload)
    msg, tool_calls = parse_constrained_tool_response(content)

    assert msg == "Task complete with zero errors."
    assert len(tool_calls) == 0


def test_parse_constrained_tool_response_markdown_wrapped():
    """Verify parsing handles markdown fenced JSON blocks."""
    raw = '```json\n{"name": "read_file", "arguments": {"path": "test.txt"}}\n```'
    thought, tool_calls = parse_constrained_tool_response(raw)

    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "read_file"
    assert json.loads(tool_calls[0]["arguments"]) == {"path": "test.txt"}


import asyncio


def test_openai_compat_llama_cpp_grammar_injection():
    """Verify that OpenAICompatAdapter automatically injects GBNF grammar for llama_cpp."""
    async def _run():
        adapter = OpenAICompatAdapter("llama_cpp", api_key="dummy", base_url="http://localhost:8080/v1")

        captured_kwargs = {}

        async def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            mock_resp = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message = MagicMock()
            mock_choice.message.tool_calls = None
            mock_choice.message.content = json.dumps({
                "name": "bash",
                "arguments": {"command": "ls"},
            })
            mock_resp.choices = [mock_choice]
            mock_resp.usage = None
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=fake_create)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            res = await adapter.chat(
                model="qwen2.5-coder",
                messages=[{"role": "user", "content": "list files"}],
                tools=SAMPLE_TOOLS,
            )

            assert "extra_body" in captured_kwargs
            assert "grammar" in captured_kwargs["extra_body"]
            assert 'func-name ::= "bash" | "read_file"' in captured_kwargs["extra_body"]["grammar"]

            # Verify output was parsed into ToolCall instance
            assert len(res.tool_calls) == 1
            assert isinstance(res.tool_calls[0], ToolCall)
            assert res.tool_calls[0].name == "bash"
            assert json.loads(res.tool_calls[0].arguments) == {"command": "ls"}

    asyncio.run(_run())
