"""
libs/aja-core/aja/gateway/client_factory.py - Centralized Client Factory & Shared Gateway Kernel.

Provides canonical exponential backoff with full jitter, loop-aware cached AsyncOpenAI
clients, dynamic project root discovery, and standard request timeouts.
"""

from __future__ import annotations

import asyncio
import os
import random
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore

# Thread/loop-safe client cache keyed by (base_url, api_key, id(event_loop))
_CLIENT_CACHE: Dict[Tuple[str, str, int], Any] = {}


def llm_timeout_s() -> float:
    """Return per-request LLM timeout in seconds from environment knob or default 120s."""
    try:
        return float(os.getenv("AJA_LLM_TIMEOUT_S", "120"))
    except ValueError:
        return 120.0


def backoff_sleep_seconds(attempt: int, max_delay: float = 30.0) -> float:
    """Return jittered exponential backoff duration in seconds.

    Bounds: [0.5x, 1.0x] * min(max_delay, 2**attempt).
    Full-jitter bounds prevent fleet-wide thundering-herd synchronization.
    """
    base = min(max_delay, 2.0 ** max(0, attempt))
    return base * (0.5 + random.random() / 2.0)


async def backoff_sleep(attempt: int, max_delay: float = 30.0) -> None:
    """Asynchronously sleep for jittered backoff seconds."""
    await asyncio.sleep(backoff_sleep_seconds(attempt, max_delay=max_delay))


def find_project_root() -> Path:
    """Find the project repository root from CWD or module location checking aja.json and .git."""
    candidates = [Path.cwd(), *Path(__file__).resolve().parents]
    for current in candidates:
        if (current / "aja.json").exists() or (current / ".git").exists():
            return current
    return Path.cwd()


def get_async_openai_client(
    base_url: str,
    api_key: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> Any:
    """Return a loop-aware, thread-safe cached AsyncOpenAI client instance.

    Recreates client if the running event loop changes, preventing cross-loop runtime errors.
    """
    if AsyncOpenAI is None:
        raise RuntimeError("The 'openai' package is required for AsyncOpenAI client creation")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    resolved_key = api_key or "dummy-local-key"
    cache_key = (base_url.rstrip("/"), resolved_key, id(loop))

    if cache_key in _CLIENT_CACHE:
        return _CLIENT_CACHE[cache_key]

    headers = {
        "HTTP-Referer": "https://github.com/aja",
        "X-Title": "AJA Swarm Toolkit",
    }
    if extra_headers:
        headers.update(extra_headers)

    client = AsyncOpenAI(
        api_key=resolved_key,
        base_url=base_url,
        default_headers=headers,
        timeout=timeout if timeout is not None else llm_timeout_s(),
        max_retries=0,  # Application layer handles retries with backoff_sleep
    )

    _CLIENT_CACHE[cache_key] = client
    return client


def clear_client_cache() -> None:
    """Clear cached AsyncOpenAI client instances (hygiene for testing)."""
    _CLIENT_CACHE.clear()
