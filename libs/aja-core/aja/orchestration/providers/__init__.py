"""
Provider adapter registry.

Maps provider names to adapter classes. Adding a provider = implementing
ProviderAdapter in a new module and registering it here.
"""

from __future__ import annotations

from typing import Dict, Optional, Type

from aja.orchestration.providers.base import ProviderAdapter

_REGISTRY: Dict[str, Type] = {}


def register_adapter(name: str, cls: Type) -> None:
    _REGISTRY[name.lower()] = cls


def get_adapter_class(provider: str) -> Optional[Type]:
    return _REGISTRY.get(provider.lower())


def available_adapters() -> list:
    return sorted(_REGISTRY.keys())


# Lazy registrations (avoid importing heavy modules at package load).
def _register_defaults() -> None:
    from aja.orchestration.providers.openai_compat import OpenAICompatAdapter

    for name in ("openai", "openrouter", "together", "groq", "nvidia",
                 "llama_cpp", "ollama", "copilot"):
        register_adapter(name, OpenAICompatAdapter)

    try:
        from aja.orchestration.providers.google_adapter import GoogleAdapter
        register_adapter("google", GoogleAdapter)
    except ImportError:
        pass

    try:
        from aja.orchestration.providers.anthropic_adapter import AnthropicAdapter
        register_adapter("anthropic", AnthropicAdapter)
    except ImportError:
        pass


_register_defaults()
