"""Schema-enforced structured output for AJA LLM gateways.

Two-strategy approach with no external dependencies:

- Strategy A (preferred): force a synthetic ``emit_result`` tool whose
  parameters ARE the target JSON schema, then extract the arguments of the
  returned tool call.
- Strategy B (fallback): if the provider ignores tools and returns plain
  content, robustly extract the outermost JSON value (fences stripped,
  brace/bracket balanced scan) from that same response.

Responses are validated by a minimal jsonschema-style validator supporting
``object`` / ``array`` / ``string`` / ``number`` / ``integer`` / ``boolean``
/ ``null`` types plus ``properties``, ``required`` and ``items``. On failure,
a single repair round-trip is attempted per remaining repair budget before
raising :class:`StructuredOutputError`.
"""

import json
import re

__all__ = [
    "StructuredOutputError",
    "structured_completion",
    "validate_against_schema",
    "extract_json_object",
]

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class StructuredOutputError(ValueError):
    """Raised when schema-conforming structured output cannot be obtained."""

    def __init__(self, message, last_raw=None, errors=None):
        super().__init__(message)
        self.last_raw = last_raw
        self.errors = list(errors or [])


def validate_against_schema(value, schema):
    """Validate ``value`` against a minimal JSON-schema subset.

    Returns a list of human-readable error strings (empty when valid).
    """
    errors = []
    _validate(value, schema or {}, "$", errors)
    return errors


def _validate(value, schema, path, errors):
    if not isinstance(schema, dict):
        return
    expected = schema.get("type")
    if expected is not None:
        if isinstance(expected, str):
            expected = [expected]
        checkers = [_TYPE_CHECKS[t] for t in expected if t in _TYPE_CHECKS]
        if checkers and not any(c(value) for c in checkers):
            errors.append(
                f"{path}: expected type {'/'.join(expected)}, "
                f"got {type(value).__name__}"
            )
            return
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required property '{req}'")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                _validate(value[key], sub or {}, f"{path}.{key}", errors)
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                _validate(item, items, f"{path}[{i}]", errors)


def _find_outermost(text):
    """Return the first balanced {...} or [...] span in ``text`` (or None)."""
    start = -1
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if start == -1:
            if ch in "{[":
                start = i
                depth = 1
            continue
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json_object(text):
    """Best-effort extraction of a JSON object/array from raw model text."""
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return None
    candidates = []
    fence_bodies = [m.group(1) for m in _FENCE_RE.finditer(text)]
    candidates.append(_find_outermost(text))
    for body in fence_bodies:
        candidates.append(_find_outermost(body))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _wrap_array_root(schema):
    """Wrap an array-root schema for synthetic-tool parameters.

    OpenAI-family Chat Completions endpoints require function ``parameters``
    to be object-rooted; array roots 400 on strict providers. Returns
    ``(parameters_schema, was_wrapped)``.
    """
    if isinstance(schema, dict) and schema.get("type") == "array":
        return (
            {
                "type": "object",
                "properties": {"items": schema},
                "required": ["items"],
            },
            True,
        )
    return schema, False


def _unwrap_wrapped_value(value, schema):
    """Inverse of :func:`_wrap_array_root` on a validated candidate."""
    if (
        isinstance(schema, dict)
        and schema.get("type") == "array"
        and isinstance(value, dict)
        and set(value.keys()) == {"items"}
    ):
        return value["items"]
    return value


def _emit_result_tool(schema):
    parameters, _ = _wrap_array_root(schema)
    return [
        {
            "type": "function",
            "function": {
                "name": "emit_result",
                "description": (
                    "Return the result conforming to the schema. The tool "
                    "arguments MUST be a single JSON value matching the "
                    "provided JSON schema exactly."
                ),
                "parameters": parameters,
            },
        }
    ]


def _extract_and_validate(response, schema):
    """Pull candidate JSON values out of a gateway response and validate.

    Returns ``(data_or_None, last_raw, errors)``.
    """
    raws = []
    if isinstance(response, dict):
        for tc in response.get("tool_calls") or []:
            if isinstance(tc, dict) and "arguments" in tc:
                raws.append(tc["arguments"])
        raws.append(response.get("content"))
    else:
        raws.append(response)

    last_raw = None
    errors = ["no parsable JSON found in model response"]
    for raw in raws:
        if raw is None:
            continue
        obj = raw if isinstance(raw, (dict, list)) else extract_json_object(raw)
        if obj is None:
            last_raw = raw if isinstance(raw, str) else json.dumps(raw)
            continue
        last_raw = obj if isinstance(obj, str) else json.dumps(obj)
        obj = _unwrap_wrapped_value(obj, schema)
        errors = validate_against_schema(obj, schema)
        if not errors:
            return obj, last_raw, []
    return None, last_raw, errors


async def structured_completion(
    gateway,
    prompt,
    schema,
    system=None,
    model=None,
    max_repair=1,
):
    """Ask ``gateway`` for output conforming to ``schema``; returns parsed dict/list.

    Raises :class:`StructuredOutputError` (with ``.last_raw`` and ``.errors``)
    after exhausting ``max_repair`` repair round-trips.
    """
    tool = _emit_result_tool(schema)
    last_raw = None
    errors = []

    async def one_round(round_prompt):
        nonlocal last_raw, errors
        kwargs = {"model": model, "prompt": round_prompt}
        if system is not None:
            kwargs["system"] = system
        response = await gateway.chat(tools=tool, **kwargs)
        data, raw, errs = _extract_and_validate(response, schema)
        if raw is not None:
            last_raw = raw
        if data is not None:
            errors = []
            return data
        errors = errs or errors
        return None

    result = await one_round(prompt)
    rounds = 0
    while result is None and rounds < max_repair:
        rounds += 1
        error_block = "\n".join(f"- {e}" for e in errors) or "- unknown"
        repair_prompt = (
            f"{prompt}\n\nYour previous response was:\n{last_raw}\n\n"
            f"It failed validation with these errors:\n{error_block}\n\n"
            "Return ONLY corrected JSON conforming to the schema."
        )
        result = await one_round(repair_prompt)

    if result is None:
        raise StructuredOutputError(
            f"Structured output failed schema validation after {rounds} "
            f"repair round(s); errors: {errors}",
            last_raw=last_raw,
            errors=errors,
        )
    return result
