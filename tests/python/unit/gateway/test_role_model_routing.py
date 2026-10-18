"""
Dual-model routing tests: the resolve_provider_model mode matrix.

Guarantees the planner-cloud / worker-local split actually reaches two
different gateways (the core of AJA's dual-model system).
"""

import pytest

from aja.llm import resolve_provider_model


CLOUD_PROVIDERS = {"google", "openai", "anthropic", "openrouter", "copilot"}


class TestHybridMode:
    """hybrid = dual-model: explicit selections honored as-is."""

    def test_worker_local_model_survives(self):
        provider, model = resolve_provider_model(
            "llama_cpp:qwen2.5-7b-instruct", "hybrid", "gemma-4-e2b", "gemini-2.5-flash"
        )
        assert (provider, model) == ("llama_cpp", "qwen2.5-7b-instruct")

    def test_planner_cloud_model_survives(self):
        provider, model = resolve_provider_model(
            "copilot:gpt-4o-mini", "hybrid", "gemma-4-e2b", "gemini-2.5-flash"
        )
        assert (provider, model) == ("copilot", "gpt-4o-mini")


class TestOnlineMode:
    def test_llama_cpp_redirects_to_cloud(self):
        """Documented protective behavior: online mode assumes no local server."""
        provider, model = resolve_provider_model(
            "llama_cpp:qwen2.5-7b-instruct", "online", "gemma-4-e2b", "gemini-2.5-flash"
        )
        assert (provider, model) == ("google", "gemini-2.5-flash")

    def test_cloud_models_untouched_online(self):
        for model_str in ("copilot:gpt-4o-mini", "google:gemini-2.0-flash"):
            provider, model = resolve_provider_model(model_str, "online", "lfm", "gemini-2.5-flash")
            assert f"{provider}:{model}" == model_str


class TestOfflineMode:
    @pytest.mark.parametrize("model_str", [
        "copilot:gpt-4o-mini",
        "google:gemini-2.0-flash",
        "openai:gpt-4o",
        "openrouter:x/y",
    ])
    def test_cloud_redirects_to_local(self, model_str):
        provider, model = resolve_provider_model(model_str, "offline", "qwen2.5-7b", "unused")
        assert provider == "llama_cpp"
        assert model == "qwen2.5-7b"

    def test_local_models_untouched_offline(self):
        provider, model = resolve_provider_model("llama_cpp:qwen2.5-7b", "offline", "qwen2.5-7b", "unused")
        assert (provider, model) == ("llama_cpp", "qwen2.5-7b")


class TestSmartDetection:
    def test_bare_local_names_route_local(self):
        for name in ("Qwen2.5-7B-Instruct", "mistral-7b", "llama-3.2", "gemma-2-9b"):
            provider, _ = resolve_provider_model(name, "hybrid", "x", "y")
            assert provider == "llama_cpp"

    def test_gemini_routes_google(self):
        provider, _ = resolve_provider_model("gemini-2.0-flash", "hybrid", "x", "y")
        assert provider == "google"

    def test_unknown_defaults_openrouter(self):
        provider, model = resolve_provider_model("some-unknown-model", "hybrid", "x", "y")
        assert provider == "openrouter"


def test_dual_model_endstate():
    """The flagship guarantee: cloud planner + local worker coexist in hybrid."""
    p1, m1 = resolve_provider_model("copilot:gpt-4o-mini", "hybrid", "qwen2.5-7b", "gemini-2.5-flash")
    p2, m2 = resolve_provider_model("llama_cpp:qwen2.5-7b", "hybrid", "qwen2.5-7b", "gemini-2.5-flash")
    assert (p1, m1) != (p2, m2)
    assert p1 in CLOUD_PROVIDERS and p2 == "llama_cpp"
