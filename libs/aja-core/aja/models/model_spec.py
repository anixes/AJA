"""
aja.models.model_spec — Model-Agnostic Capability and Specification Engine.
=============================================================================
Defines standard model specifications, capabilities (chat, vision, tools, code),
and tier classification (local vs cloud). Enables capability-based auto-routing
without relying on rigid job titles like 'planner' or 'worker'.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set


class ModelCapability(str, enum.Enum):
    """Core capabilities an AI model or runtime may provide."""
    CHAT = "chat"
    VISION = "vision"
    CODE = "code"
    TOOLS = "tools"
    EMBEDDING = "embedding"


class ModelTier(str, enum.Enum):
    """Physical execution location of the model."""
    LOCAL = "local"
    CLOUD = "cloud"


_LOCAL_PROVIDERS = {"llama_cpp", "ollama", "lm_studio", "local"}
_VISION_KEYWORDS = {"-vl-", "_vl_", "vision", "vl", "llava", "multimodal", "omni", "showui"}
_CODE_KEYWORDS = {"coder", "code", "starcoder", "deepseek-coder", "codex"}


def infer_capabilities(model_name: str, provider: str = "") -> Set[ModelCapability]:
    """
    Infer supported capabilities from model identifier and provider.
    """
    caps: Set[ModelCapability] = {ModelCapability.CHAT}
    low_name = model_name.lower()
    low_provider = provider.lower()

    # 1. Vision Capability
    if any(k in low_name for k in _VISION_KEYWORDS):
        caps.add(ModelCapability.VISION)
    elif low_provider in ("google", "gemini"):
        # Most modern Gemini models support vision
        caps.add(ModelCapability.VISION)
    elif "gpt-4o" in low_name or "claude-3" in low_name or "claude-sonnet" in low_name or "claude-opus" in low_name:
        caps.add(ModelCapability.VISION)

    # 2. Code Capability
    if any(k in low_name for k in _CODE_KEYWORDS) or low_provider in ("copilot", "github-copilot"):
        caps.add(ModelCapability.CODE)

    # 3. Native Tool Calling
    # Cloud providers and modern instruct models support tools
    if low_provider in ("google", "openai", "copilot", "anthropic", "openrouter"):
        caps.add(ModelCapability.TOOLS)
    elif "instruct" in low_name or "coder" in low_name or "tools" in low_name or "hermes" in low_name:
        caps.add(ModelCapability.TOOLS)

    # 4. Embeddings
    if "embed" in low_name or "bge" in low_name or "nomic" in low_name:
        caps.add(ModelCapability.EMBEDDING)

    return caps


@dataclass
class ModelSpec:
    """Standardized descriptor for any model across local and cloud engines."""
    uri: str
    name: str
    provider: str
    tier: ModelTier
    capabilities: Set[ModelCapability] = field(default_factory=lambda: {ModelCapability.CHAT})
    endpoint: Optional[str] = None
    context_window: Optional[int] = None
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def model_name(self) -> str:
        return self.name

    @property
    def has_vision(self) -> bool:
        return ModelCapability.VISION in self.capabilities

    @property
    def has_tools(self) -> bool:
        return ModelCapability.TOOLS in self.capabilities

    @property
    def has_code(self) -> bool:
        return ModelCapability.CODE in self.capabilities

    @property
    def is_local(self) -> bool:
        return self.tier == ModelTier.LOCAL

    @property
    def is_cloud(self) -> bool:
        return self.tier == ModelTier.CLOUD

    @property
    def quantization(self) -> Optional[str]:
        return self.details.get("quantization")

    def supports(self, capability: ModelCapability | str) -> bool:
        cap_enum = ModelCapability(capability) if isinstance(capability, str) else capability
        return cap_enum in self.capabilities

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "provider": self.provider,
            "tier": self.tier.value,
            "capabilities": [c.value for c in self.capabilities],
            "endpoint": self.endpoint,
            "context_window": self.context_window,
            "details": self.details,
        }


def parse_model_spec(model_uri: str, endpoint: Optional[str] = None) -> ModelSpec:
    """
    Parse any model URI or name into a strongly-typed ModelSpec.
    Handles 'provider:model_name', local filenames, or plain aliases.
    """
    cleaned = (model_uri or "").strip()
    if not cleaned:
        cleaned = "google:gemini-2.0-flash"

    if ":" in cleaned:
        provider, model_name = cleaned.split(":", 1)
        provider = provider.strip().lower()
        model_name = model_name.strip()
    else:
        model_name = cleaned
        # Auto-detect provider
        low = model_name.lower()
        if low.endswith(".gguf") or "gemma" in low or "qwen" in low or "llama" in low or "lfm" in low:
            provider = "llama_cpp"
        elif "gemini" in low:
            provider = "google"
        elif "copilot" in low:
            provider = "copilot"
        elif "claude" in low:
            provider = "anthropic"
        else:
            provider = "openai"

    tier = ModelTier.LOCAL if provider in _LOCAL_PROVIDERS else ModelTier.CLOUD
    caps = infer_capabilities(model_name, provider=provider)

    # Friendly clean display name
    clean_name = re.sub(r"\.gguf$", "", model_name, flags=re.IGNORECASE)

    details: Dict[str, Any] = {}
    quant_match = re.search(r"[-_](q[0-9]_[k0-9_msal]+|f16|f32|q8_0|q4_0|q4_1)", model_name, re.IGNORECASE)
    if quant_match:
        details["quantization"] = quant_match.group(1).upper()

    return ModelSpec(
        uri=f"{provider}:{model_name}" if not cleaned.startswith(f"{provider}:") else cleaned,
        name=clean_name,
        provider=provider,
        tier=tier,
        capabilities=caps,
        endpoint=endpoint,
        details=details,
    )
