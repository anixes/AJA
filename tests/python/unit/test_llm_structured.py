"""Tests for aja.llm_structured — schema-enforced structured output."""

import pytest

from aja.llm_structured import (
    StructuredOutputError,
    extract_json_object,
    structured_completion,
    validate_against_schema,
)

SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["answer"],
}

PLAN_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"id": {}, "task": {"type": "string"}},
        "required": ["id", "task"],
    },
}


class FakeGateway:
    """Mock gateway mimicking LLMGateway.chat(tools=...) semantics."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, model=None, prompt=None, system=None, tools=None, **kwargs):
        self.calls.append({"prompt": prompt, "system": system, "tools": tools})
        if not self.responses:
            raise AssertionError("FakeGateway exhausted responses")
        return self.responses.pop(0)


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


# ── Strategy A ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_strategy_a_tool_call_args_returned_no_repair():
    gw = FakeGateway([
        {
            "content": "",
            "tool_calls": [
                {
                    "name": "emit_result",
                    "arguments": '{"answer": "42", "confidence": 0.9}',
                }
            ],
        }
    ])
    result = await structured_completion(gw, "What is 6x7?", SIMPLE_SCHEMA, max_repair=1)
    assert result == {"answer": "42", "confidence": 0.9}
    assert len(gw.calls) == 1
    assert gw.calls[0]["tools"][0]["function"]["name"] == "emit_result"
    assert gw.calls[0]["tools"][0]["function"]["parameters"] == SIMPLE_SCHEMA


@pytest.mark.anyio
async def test_strategy_a_dict_arguments_accepted():
    gw = FakeGateway([
        {"content": "", "tool_calls": [{"name": "emit_result", "arguments": '[{"task": "x", "id": 1}]'}]}
    ])
    result = await structured_completion(gw, "plan it", PLAN_SCHEMA)
    assert result == [{"id": 1, "task": "x"}]


# ── Strategy B ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_strategy_b_content_fallback_when_no_tool_calls():
    gw = FakeGateway([
        {
            "content": 'Sure!\n```json\n{"answer": "hello"}\n```\nHope that helps.',
            "tool_calls": [],
        }
    ])
    result = await structured_completion(gw, "greet", SIMPLE_SCHEMA)
    assert result == {"answer": "hello"}
    assert len(gw.calls) == 1


@pytest.mark.anyio
async def test_strategy_b_plain_string_response():
    gw = FakeGateway(['noise before {"answer":"a","confidence":1} noise after'])
    result = await structured_completion(gw, "p", SIMPLE_SCHEMA)
    assert result == {"answer": "a", "confidence": 1}


# ── Repair round-trip ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_repair_round_trip_exactly_one_repair_call():
    gw = FakeGateway([
        {"content": '{"wrong_key": true}', "tool_calls": []},
        {"content": '{"answer": "fixed"}', "tool_calls": []},
    ])
    result = await structured_completion(gw, "do it", SIMPLE_SCHEMA, max_repair=1)
    assert result == {"answer": "fixed"}
    assert len(gw.calls) == 2
    repair_prompt = gw.calls[1]["prompt"]
    assert "Your previous response" in repair_prompt
    assert "missing required property" in repair_prompt or "required" in repair_prompt
    assert "Return ONLY corrected JSON" in repair_prompt


# ── Exhausted repairs ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_exhausted_repairs_raise_structured_output_error():
    bad = {"content": '{"nope": 1}', "tool_calls": []}
    gw = FakeGateway([bad, bad])
    with pytest.raises(StructuredOutputError) as exc_info:
        await structured_completion(gw, "p", SIMPLE_SCHEMA, max_repair=1)
    err = exc_info.value
    assert isinstance(err, ValueError)
    assert err.errors, "errors must be populated"
    assert any("required" in e for e in err.errors)
    assert err.last_raw and '"nope"' in str(err.last_raw)
    assert len(gw.calls) == 2  # initial + one repair


@pytest.mark.anyio
async def test_zero_repair_budget_single_call():
    gw = FakeGateway([{"content": "garbage", "tool_calls": []}])
    with pytest.raises(StructuredOutputError):
        await structured_completion(gw, "p", SIMPLE_SCHEMA, max_repair=0)
    assert len(gw.calls) == 1


# ── Minimal validator ───────────────────────────────────────────────────────


def test_validator_valid_nested_object():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "meta": {
                "type": "object",
                "properties": {"depth": {"type": "integer"}},
                "required": ["depth"],
            },
        },
        "required": ["name", "meta"],
    }
    value = {"name": "x", "tags": ["a", "b"], "meta": {"depth": 3}}
    assert validate_against_schema(value, schema) == []


def test_validator_wrong_type_reports_path():
    errors = validate_against_schema({"answer": 5}, SIMPLE_SCHEMA)
    assert errors == ["$.answer: expected type string, got int"]


def test_validator_missing_required():
    errors = validate_against_schema({"confidence": 0.5}, SIMPLE_SCHEMA)
    assert "$: missing required property 'answer'" in errors


def test_validator_array_item_types():
    schema = {"type": "array", "items": {"type": "string"}}
    errors = validate_against_schema(["ok", 3], schema)
    assert errors == ["$[1]: expected type string, got int"]


def test_validator_number_rejects_bool_and_top_level_type():
    assert validate_against_schema(True, {"type": "number"})
    assert validate_against_schema("text", {"type": "object"})
    assert validate_against_schema({"a": 1}, {"type": "array"})
    assert validate_against_schema([1, 2], {"type": "array"}) == []
    assert validate_against_schema(2.5, {"type": "number"}) == []


def test_extract_json_object_fenced_and_balanced():
    assert extract_json_object('```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert extract_json_object('x {"a": "b}c"} y') == {"a": "b}c"}
    assert extract_json_object("no json here") is None
    assert extract_json_object('[{"id": 1}]') == [{"id": 1}]
