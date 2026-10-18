"""
aja.gateway - Universal Gateway subsystem for AJA.
"""

from .client_factory import (
    backoff_sleep,
    backoff_sleep_seconds,
    llm_timeout_s,
    find_project_root,
    get_async_openai_client,
    clear_client_cache,
)

def __getattr__(name: str):
    if name == "UnifiedGateway":
        from .orchestrator import UnifiedGateway
        return UnifiedGateway
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "UnifiedGateway",
    "backoff_sleep",
    "backoff_sleep_seconds",
    "llm_timeout_s",
    "find_project_root",
    "get_async_openai_client",
    "clear_client_cache",
]
