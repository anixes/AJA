"""
Test Gateway Agnosticism & Provider Adapter Decoupling.
======================================================
Verifies that LLMGateway adheres strictly to the Triad-mandated
model-agnostic and provider-agnostic principles:
- Zero hardcoded vendor model name checks in LLMGateway.chat
- ProviderAdapter registry correctly resolves specialized adapters
- CopilotAdapter cleanly encapsulates Copilot protocol details
"""

import inspect
import pytest
from aja.orchestration.gateway import LLMGateway
from aja.orchestration.providers import get_adapter_class
from aja.orchestration.providers.copilot_adapter import CopilotAdapter


def test_gateway_no_vendor_model_heuristics():
    """
    Assert that LLMGateway.chat does not contain hardcoded model checks
    such as 'gpt-4o', 'gpt-5-mini', or 'claude'.
    """
    chat_source = inspect.getsource(LLMGateway.chat)
    forbidden_heuristics = [
        "gpt-4o",
        "gpt-5-mini",
        '"claude"',
        "'claude'",
        "v1/responses",
    ]
    for heuristic in forbidden_heuristics:
        assert heuristic not in chat_source, (
            f"LLMGateway.chat violates model agnosticism by containing: {heuristic}"
        )


def test_copilot_adapter_registered():
    """
    Verify that CopilotAdapter is properly registered for 'copilot'
    in the provider adapter registry.
    """
    adapter_cls = get_adapter_class("copilot")
    assert adapter_cls is not None
    assert issubclass(adapter_cls, CopilotAdapter)


def test_copilot_adapter_model_normalization():
    """
    Verify that CopilotAdapter normalizes aliases internally without
    polluting the core gateway.
    """
    adapter = CopilotAdapter(api_key="test-key", base_url="https://api.githubcopilot.com")
    assert adapter._normalize_copilot_model("copilot") == "gpt-4o"
    assert adapter._normalize_copilot_model("copilot-mini") == "gpt-4o-mini"
    assert adapter._normalize_copilot_model("copilot:gpt-4o") == "gpt-4o"
    assert adapter._normalize_copilot_model("o3-mini") == "o3-mini"


def test_copilot_adapter_responses_endpoint_selection():
    """
    Verify that responses API heuristics are isolated to CopilotAdapter.
    """
    adapter = CopilotAdapter(api_key="test-key", base_url="https://api.githubcopilot.com")
    assert adapter._should_use_responses_api("claude-3.5-sonnet", []) is True
    assert adapter._should_use_responses_api("gpt-4o", []) is False
    assert adapter._should_use_responses_api("gpt-4o-mini", []) is False


@pytest.mark.anyio
async def test_copilot_adapter_non_200_raises(monkeypatch):
    """
    Verify that CopilotAdapter._chat_responses_api raises an explicit error
    with status_code on HTTP errors, rather than returning empty content.
    """
    from unittest.mock import AsyncMock, MagicMock

    adapter = CopilotAdapter(api_key="test-key", base_url="https://api.githubcopilot.com")

    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Internal Server Error")

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value = mock_resp
    mock_session.closed = False

    monkeypatch.setattr(adapter, "_get_http_session", AsyncMock(return_value=mock_session))

    with pytest.raises(RuntimeError) as exc_info:
        await adapter._chat_responses_api(
            model="claude-3.5-sonnet",
            messages=[{"role": "user", "content": "hello"}],
            system="",
            tools=None,
            temperature=None,
            extra_body=None,
            retries=1,
        )

    assert "500" in str(exc_info.value)
    assert getattr(exc_info.value, "status_code", None) == 500


def test_model_router_isinstance_and_delegation():
    """
    Verify that ModelRouter is an instance of LLMGateway and delegates cleanly.
    """
    from aja.llm import ModelRouter

    router = ModelRouter(default_model="gate:qwen")
    assert isinstance(router, LLMGateway)
    assert router.active_model == "gate:qwen"
    assert router.provider == "gate"

    # Private attributes should not trigger __getattr__
    with pytest.raises(AttributeError):
        _ = router._non_existent_private_attr


def test_gateway_caches_adapters():
    """
    Verify that LLMGateway caches adapter instances on self._adapters.
    """
    gw = LLMGateway(provider="gate", api_key="test-key")
    assert hasattr(gw, "_adapters")
    assert isinstance(gw._adapters, dict)

