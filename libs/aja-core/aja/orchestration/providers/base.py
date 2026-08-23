"""
Provider adapter protocol and shared response types.

Every LLM provider implements ProviderAdapter, translating its native wire
format to/from the common LLMResponse currency. The gateway resolves the
correct adapter from the registry — zero provider-specific branches there.

Adding a provider = implementing one class in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""
    id: str
    name: str
    arguments: str  # JSON-encoded string (OpenAI convention)


@dataclass
class LLMResponse:
    """Normalized LLM response across all providers."""
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    model: str = ""
    usage: Dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class ProviderAdapter(Protocol):
    """Protocol every provider adapter must satisfy."""

    provider_name: str

    async def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: str = "You are a helpful assistant.",
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        retries: int = 1,
    ) -> LLMResponse:
        """Single completion round-trip. Returns normalized LLMResponse."""
        ...

    async def stream(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: str = "You are a helpful assistant.",
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """Yields content chunks as they arrive."""
        ...

    async def close(self) -> None:
        """Release HTTP sessions/clients."""
        ...
