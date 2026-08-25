"""Night-shift Wave 2 E6 regression tests.

Covers:
- AnthropicAdapter accepts (and uses) base_url like its siblings (L1#1).
- Registry-wide adapter constructor conformance (api_key + base_url).
- copilot_auth preserves env-sourced tokens on invalidation; only
  process-written env keys are removed (L1#2).
- Array-root schema wrap/unwrap in llm_structured (L3#1).
- Provider-safe tool-name sanitization bijection in native.py (L3#2).
- NativeToolRegistry.execute raises ToolSignatureError on signature drift
  instead of returning a misclassified string (L3#3).
"""

import asyncio
import os

import pytest


# --------------------------------------------------------------------------- #
# Fix 1 — AnthropicAdapter base_url acceptance (L1#1)
# --------------------------------------------------------------------------- #


def test_anthropic_adapter_accepts_base_url():
    from aja.orchestration.providers.anthropic_adapter import (
        ANTHROPIC_BASE_URL,
        AnthropicAdapter,
    )

    adapter = AnthropicAdapter(api_key="x", base_url="https://proxy.example.com")
    assert adapter.base_url == "https://proxy.example.com"

    default_adapter = AnthropicAdapter(api_key="x")
    assert default_adapter.base_url == ANTHROPIC_BASE_URL

    empty_adapter = AnthropicAdapter(api_key="x", base_url="")
    assert empty_adapter.base_url == ANTHROPIC_BASE_URL


def test_registry_adapters_constructor_conformance():
    """Every registered adapter must accept (api_key=..., base_url=...).

    The gateway instantiates all adapters uniformly (gateway.py); an adapter
    that rejects base_url is silently unreachable (TypeError -> legacy path).
    """
    from aja.orchestration.providers import _REGISTRY, _register_defaults

    _register_defaults()
    assert "anthropic" in _REGISTRY
    for name, cls in _REGISTRY.items():
        # OpenAICompatAdapter takes provider positionally; the gateway passes
        # api_key/base_url as kwargs uniformly.
        instance = cls(
            name, api_key="test-key", base_url="https://example.invalid"
        ) if name == "openai_compat" or cls.__name__ == "OpenAICompatAdapter" else cls(
            api_key="test-key", base_url="https://example.invalid"
        )
        assert instance is not None, f"adapter {name} failed construction"


def test_anthropic_build_body_uses_translated_tools():
    from aja.orchestration.providers.anthropic_adapter import AnthropicAdapter

    tools = [
        {
            "type": "function",
            "function": {
                "name": "browser__extract_markdown",
                "description": "d",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    body = AnthropicAdapter._build_body(
        model="claude-3-5-sonnet",
        messages=[{"role": "user", "content": "hi"}],
        system="sys",
        tools=tools,
        temperature=None,
        extra_body=None,
        max_tokens=1024,
    )
    assert body["tools"][0]["name"] == "browser__extract_markdown"
    assert body["tools"][0]["input_schema"]["type"] == "object"


# --------------------------------------------------------------------------- #
# Fix 2 — copilot_auth env-token preservation + singleflight (L1#2)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def clean_copilot_state(monkeypatch):
    import aja.copilot_auth as ca

    monkeypatch.setattr(ca, "_CACHED_RAW_TOKEN", None)
    monkeypatch.setattr(ca, "_jwt_cache", {})
    saved_written = set(ca._ENV_TOKENS_WRITTEN)
    ca._ENV_TOKENS_WRITTEN.clear()
    yield ca
    ca._ENV_TOKENS_WRITTEN.clear()
    ca._ENV_TOKENS_WRITTEN.update(saved_written)


def test_invalidate_preserves_env_sourced_token(clean_copilot_state, monkeypatch):
    ca = clean_copilot_state
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "gho_operator_token")

    ca.invalidate_copilot_cache()

    assert os.environ.get("COPILOT_GITHUB_TOKEN") == "gho_operator_token"
    # Resolution can still find the operator's token after invalidation.
    token, source = ca.resolve_copilot_token()
    assert token == "gho_operator_token"
    assert source == "COPILOT_GITHUB_TOKEN"


def test_invalidate_removes_only_process_written_env(clean_copilot_state, monkeypatch):
    ca = clean_copilot_state
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)

    ca._ENV_TOKENS_WRITTEN.add("COPILOT_GITHUB_TOKEN")
    os.environ["COPILOT_GITHUB_TOKEN"] = "gho_process_written"
    try:
        ca.invalidate_copilot_cache()
        assert "COPILOT_GITHUB_TOKEN" not in os.environ
    finally:
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)


def test_env_sourced_token_survives_repeated_refresh_cycles(
    clean_copilot_state, monkeypatch
):
    """The VPS death-spiral repro: repeated 401 invalidations must never
    destroy an env-sourced raw token."""
    ca = clean_copilot_state
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "gho_vps_token")

    for _ in range(3):
        ca.invalidate_copilot_cache()
        token, source = ca.resolve_copilot_token()
        assert token == "gho_vps_token"
        assert source == "COPILOT_GITHUB_TOKEN"


# --------------------------------------------------------------------------- #
# Fix 4 — llm_structured array-root wrap/unwrap (L3#1)
# --------------------------------------------------------------------------- #

PLAN_SCHEMA_LIKE = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"id": {}, "task": {"type": "string"}},
        "required": ["id", "task"],
    },
}


def test_emit_result_tool_wraps_array_root():
    from aja.llm_structured import _emit_result_tool

    tool = _emit_result_tool(PLAN_SCHEMA_LIKE)
    params = tool[0]["function"]["parameters"]
    assert params["type"] == "object"
    assert params["required"] == ["items"]
    assert params["properties"]["items"] == PLAN_SCHEMA_LIKE


def test_emit_result_tool_leaves_object_root_untouched():
    from aja.llm_structured import _emit_result_tool

    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    params = _emit_result_tool(schema)[0]["function"]["parameters"]
    assert params is schema


def test_extract_and_validate_unwraps_array_root_result():
    from aja.llm_structured import _extract_and_validate

    response = {
        "tool_calls": [
            {
                "arguments": {
                    "items": [{"id": 1, "task": "a"}, {"id": 2, "task": "b"}]
                }
            }
        ]
    }
    data, _, errors = _extract_and_validate(response, PLAN_SCHEMA_LIKE)
    assert errors == []
    assert isinstance(data, list)
    assert data[0]["task"] == "a"


def test_extract_and_validate_accepts_bare_array_too():
    from aja.llm_structured import _extract_and_validate

    wrapped_response = {
        "tool_calls": [
            {"arguments": [{"id": 1, "task": "only"}]},
        ]
    }
    data, _, errors = _extract_and_validate(wrapped_response, PLAN_SCHEMA_LIKE)
    assert errors == []
    assert data == [{"id": 1, "task": "only"}]


class _FakeGateway:
    """Returns a canned tool_call carrying ``payload`` as arguments."""

    def __init__(self, payload):
        self.payload = payload
        self.captured_tools = None

    async def chat(self, *, model=None, prompt=None, system=None, tools=None, **kw):
        self.captured_tools = tools
        return {
            "content": "",
            "tool_calls": [{"id": "t1", "name": "emit_result", "arguments": self.payload}],
        }


def test_structured_completion_end_to_end_array_root():
    from aja.llm_structured import structured_completion

    gw = _FakeGateway({"items": [{"id": 1, "task": "x"}]})
    result = asyncio.run(
        structured_completion(gw, "plan please", PLAN_SCHEMA_LIKE)
    )
    assert result == [{"id": 1, "task": "x"}]
    # The advertised parameters must be object-rooted for strict endpoints.
    assert gw.captured_tools[0]["function"]["parameters"]["type"] == "object"


# --------------------------------------------------------------------------- #
# Fix 5 — native.py tool-name sanitization bijection (L3#2)
# --------------------------------------------------------------------------- #

_SAFE_NAME_RE = r"^[a-zA-Z0-9_-]{1,64}$"


def test_sanitize_dotted_names_are_provider_safe():
    from aja.orchestration.tools.native import sanitize_tool_name

    import re

    for name in (
        "browser.extract_markdown",
        "browser.wait_for_selector",
        "desktop.move_mouse",
        "mcp.my_server.my.tool",
    ):
        safe = sanitize_tool_name(name)
        assert re.match(_SAFE_NAME_RE, safe), f"{name} -> {safe}"
        assert "." not in safe


def test_sanitization_bijection_roundtrip():
    from aja.orchestration.tools.native import (
        desanitize_tool_name,
        sanitize_tool_name,
    )

    names = [
        "browser.extract_markdown",
        "browser.navigate",
        "mcp.filesystem.read_file",
        "read_file",
        "run_shell_command",
    ]
    mapping = {n: sanitize_tool_name(n) for n in names}
    # Injective: distinct originals map to distinct safe names.
    assert len(set(mapping.values())) == len(names)
    for original, safe in mapping.items():
        assert desanitize_tool_name(safe) == original


def test_sanitize_long_mcp_names_capped_and_reversible():
    from aja.orchestration.tools.native import (
        desanitize_tool_name,
        sanitize_tool_name,
    )

    import re

    long_name = "mcp." + "very_long_server_name_" * 3 + "tool_with_long_name"
    safe = sanitize_tool_name(long_name)
    assert len(safe) <= 64
    assert re.match(_SAFE_NAME_RE, safe)
    assert desanitize_tool_name(safe) == long_name


def test_get_schemas_contain_no_invalid_names():
    from aja.orchestration.tools.native import NativeToolRegistry

    import re

    registry = NativeToolRegistry()
    schemas = registry.get_schemas(interactive=False)
    seen = set()
    for t in schemas:
        name = t["function"]["name"]
        assert re.match(_SAFE_NAME_RE, name), f"unsafe advertised name: {name}"
        seen.add(name)
    assert len(seen) == len(schemas), "duplicate sanitized names in get_schemas()"


def test_dispatch_reverse_maps_sanitized_names():
    from aja.orchestration.tools.native import NativeToolRegistry, sanitize_tool_name

    registry = NativeToolRegistry()
    safe_name = sanitize_tool_name("browser.navigate")
    schema = next(
        t["function"]
        for t in registry.get_schemas(interactive=False)
        if t["function"]["name"] == safe_name
    )
    activity = registry.dispatch(
        schema["name"], {"url": "https://example.com"}, trace_id="t"
    )
    # Activity must carry the ORIGINAL dotted tool name downstream.
    assert activity.tool == "browser.navigate"
    assert activity.metadata["schema_name"] == "browser.navigate"


def test_execute_reverse_maps_and_runs_python_tool():
    from aja.orchestration.tools.native import NativeToolRegistry

    registry = NativeToolRegistry()
    result = registry.execute("get_datetime", {})
    assert "Current Time" in result


# --------------------------------------------------------------------------- #
# Fix 6 — signature drift raises instead of misclassified success (L3#3)
# --------------------------------------------------------------------------- #


def test_execute_raises_on_signature_drift():
    from aja.orchestration.tools.native import NativeToolRegistry, ToolSignatureError

    registry = NativeToolRegistry()

    with pytest.raises(ToolSignatureError):
        registry.execute("read_file", {})  # missing required 'path'

    with pytest.raises(ToolSignatureError):
        registry.execute("read_file", {"path": "x", "bogus_kwarg": 1})

    with pytest.raises(ToolSignatureError):
        registry.execute("write_file", {"path": 12345})  # missing 'content'


def test_execute_still_returns_string_for_tool_level_failures():
    from aja.orchestration.tools.native import NativeToolRegistry

    registry = NativeToolRegistry()
    # Valid signature; tool-level error path returns a string as before.
    result = registry.execute("read_file", {"path": "definitely_missing_file_xyz.txt"})
    assert result.startswith("Error")


def test_signature_drift_raises_through_registry_contract():
    """Direct contract: execute raises so callers (ActivityRuntime) journal
    TOOL_FAILED instead of TOOL_COMPLETED(success=True)."""
    from aja.orchestration.tools.native import NativeToolRegistry

    registry = NativeToolRegistry()
    with pytest.raises(TypeError):
        registry.execute("read_file", {})
