"""
errors.py — Error taxonomy and classification for LLM Gateway and Direct Loop.
==============================================================================
Provides structured classification for error types, distinguishing fallback-eligible
errors (rate limits, outages, 404s, context overflows) from fatal non-retryable errors.
Strictly model-agnostic.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence, Any


class GatewayError(Exception):
    """Base error for LLM Gateway interactions."""
    is_fallback_candidate: bool = False
    is_fatal: bool = False

    def __init__(
        self,
        message: str,
        model: str = "",
        status_code: Optional[int] = None,
        cause: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.model = model
        self.status_code = status_code
        self.cause = cause


class RateLimitError(GatewayError):
    """429 Rate Limit or Quota Exceeded."""
    is_fallback_candidate = True


class ProviderUnavailableError(GatewayError):
    """5xx Server Outage / Unavailability."""
    is_fallback_candidate = True


class ModelNotFoundError(GatewayError):
    """404 Model Not Found or Deprecated."""
    is_fallback_candidate = True


class ContextOverflowError(GatewayError):
    """Prompt / Context Length Exceeded."""
    is_fallback_candidate = True


class FatalRequestError(GatewayError):
    """Fatal client errors (e.g. invalid syntax, schema violation) that must not fallback."""
    is_fatal = True
    is_fallback_candidate = False


class ChainExhaustedError(RuntimeError):
    """All candidate models in the fallback chain were tried and failed."""
    def __init__(self, chain: Sequence[str], errors: Sequence[BaseException]):
        self.chain = list(chain)
        self.errors = list(errors)
        super().__init__(
            f"All models in fallback chain {self.chain} failed. Errors: {[repr(e) for e in self.errors]}"
        )


_RATE_LIMIT_PATTERNS = (
    r"\b429\b",
    r"rate[_\s]limit",
    r"\bquota\b",
    r"resource[_\s]exhausted",
    r"too many requests",
)

_UNAVAILABLE_PATTERNS = (
    r"\b500\b",
    r"\b502\b",
    r"\b503\b",
    r"\b504\b",
    r"overloaded",
    r"service[_\s]unavailable",
    r"bad[_\s]gateway",
    r"gateway[_\s]timeout",
    r"server[_\s]error",
    r"internal[_\s]error",
    r"\btimeout\b",
    r"timed[_\s]out",
    r"connection[_\s](?:refused|reset|error|closed)",
    r"broken[_\s]pipe",
    r"network[_\s]error",
)

_NOT_FOUND_PATTERNS = (
    r"\b404\b",
    r"not[_\s]found",
    r"does not exist",
    r"deprecat",
    r"unsupported model",
    r"model_not_found",
)

_CONTEXT_OVERFLOW_PATTERNS = (
    r"context[_\s]length",
    r"maximum context",
    r"token[_\s]limit",
    r"max[_\s]tokens",
    r"prompt[_\s]too[_\s]long",
    r"request_too_large",
    r"context_window_exceeded",
)


def classify_gateway_error(exc: Exception, model: str = "") -> GatewayError:
    """Classify an arbitrary exception (HTTP client exception, OpenAI APIError,
    aiohttp/httpx error, timeout, network glitch, etc.) into a categorized GatewayError.
    """
    if isinstance(exc, GatewayError):
        if not exc.model and model:
            exc.model = model
        return exc

    # Inspect native Python / asyncio network and timeout exceptions
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return ProviderUnavailableError(
            f"Network timeout/connection error on {model}: {exc}",
            model=model,
            status_code=504,
            cause=exc,
        )

    # Inspect status_code attribute if present (e.g. httpx.HTTPStatusError, openai.APIStatusError)
    status_code: Optional[int] = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "status", None)

    msg = str(exc)
    msg_lower = msg.lower()

    # 1. Context Overflow
    for pattern in _CONTEXT_OVERFLOW_PATTERNS:
        if re.search(pattern, msg_lower):
            return ContextOverflowError(
                f"Context window overflow on {model}: {msg}",
                model=model,
                status_code=status_code,
                cause=exc,
            )

    # 2. Rate Limit / Quota
    if status_code == 429:
        return RateLimitError(
            f"Rate limited (429) on {model}: {msg}",
            model=model,
            status_code=429,
            cause=exc,
        )
    for pattern in _RATE_LIMIT_PATTERNS:
        if re.search(pattern, msg_lower):
            return RateLimitError(
                f"Rate limit / quota error on {model}: {msg}",
                model=model,
                status_code=status_code,
                cause=exc,
            )

    # 3. Model Not Found / Deprecated
    if status_code == 404:
        return ModelNotFoundError(
            f"Model not found (404) on {model}: {msg}",
            model=model,
            status_code=404,
            cause=exc,
        )
    for pattern in _NOT_FOUND_PATTERNS:
        if re.search(pattern, msg_lower):
            return ModelNotFoundError(
                f"Model unavailable on {model}: {msg}",
                model=model,
                status_code=status_code,
                cause=exc,
            )

    # 4. Provider Unavailable (5xx & timeouts)
    if status_code and 500 <= status_code <= 599:
        return ProviderUnavailableError(
            f"Provider service unavailable ({status_code}) on {model}: {msg}",
            model=model,
            status_code=status_code,
            cause=exc,
        )
    for pattern in _UNAVAILABLE_PATTERNS:
        if re.search(pattern, msg_lower):
            return ProviderUnavailableError(
                f"Provider unavailable on {model}: {msg}",
                model=model,
                status_code=status_code,
                cause=exc,
            )

    # 5. Default Fatal / Unknown Error
    return FatalRequestError(
        f"LLM request error on {model}: {msg}",
        model=model,
        status_code=status_code,
        cause=exc,
    )
