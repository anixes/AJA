"""
OpenAI-compatible provider adapter.

Implements ProviderAdapter against any Chat Completions endpoint
(OpenAI, OpenRouter, Groq, Together, NVIDIA, llama.cpp, Ollama, Copilot).
Extracted from the legacy gateway monolith so providers live behind the
adapter protocol instead of provider-specific branches.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any, AsyncIterator, Dict, List, Optional

from aja.utils.redact import redact_secrets

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - openai is a core dep, defensive only
    AsyncOpenAI = None

from aja.orchestration.providers.base import LLMResponse, ToolCall

logger = logging.getLogger(__name__)

# Mirrors the gateway's known-provider table. Env-overridable for local servers.
PROVIDER_BASE_URLS: Dict[str, str] = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "llama_cpp": os.getenv("LLAMA_CPP_URL", "http://localhost:8080/v1"),
    "ollama": os.getenv("OLLAMA_URL", "http://localhost:11434/v1"),
    "copilot": "https://api.githubcopilot.com",
}

_PROVIDER_ENV_KEYS: Dict[str, tuple] = {
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "nvidia": ("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"),
    "together": ("TOGETHER_API_KEY",),
    "copilot": ("COPILOT_GITHUB_TOKEN",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY", "AI_KEY"),
}

# Deterministic client errors that will fail identically on retry.
_NON_RETRYABLE_STATUS = (400, 404, 422)


def _llm_timeout_s() -> float:
    """Per-request LLM timeout (seconds). The openai SDK default is 600s."""
    try:
        return float(os.getenv("AJA_LLM_TIMEOUT_S", "120"))
    except ValueError:
        return 120.0


def _backoff_sleep_seconds(attempt: int) -> float:
    """Jittered exponential backoff in [0.5x, 1.0x] of min(30, 2**attempt)."""
    return min(30.0, 2.0 ** attempt) * (0.5 + random.random() / 2.0)


async def _backoff_sleep(attempt: int) -> None:
    await asyncio.sleep(_backoff_sleep_seconds(attempt))


class OpenAICompatAdapter:
    """ProviderAdapter over any OpenAI-compatible Chat Completions endpoint."""

    def __init__(self, provider: str, api_key: str = "", base_url: str = ""):
        self.provider_name = (provider or "openai").lower()
        self.provider = self.provider_name
        self.base_url = (
            base_url
            or PROVIDER_BASE_URLS.get(self.provider)
            or ""
        ).rstrip("/")
        if not self.base_url:
            raise ValueError(
                f"Unknown provider '{provider}'. Please provide a base_url for custom endpoints."
            )
        self.api_key = self._resolve_api_key(api_key)
        self._client: Optional["AsyncOpenAI"] = None
        self._client_loop = None

    # ------------------------------------------------------------------ auth

    def _resolve_api_key(self, api_key: str) -> str:
        if api_key:
            raw_key = api_key
        else:
            env_names = _PROVIDER_ENV_KEYS.get(self.provider, ())
            raw_key = next(
                (os.getenv(name) for name in env_names if os.getenv(name)), ""
            )

        if self.provider == "copilot":
            return self._resolve_copilot_key(raw_key)

        return raw_key or "no-key-required"

    def _resolve_copilot_key(self, raw_key: str) -> str:
        """Resolve COPILOT_GITHUB_TOKEN / keyring into an exchange API token."""
        try:
            from aja.copilot_auth import (
                copilot_request_headers,
                get_copilot_api_token,
                resolve_copilot_token,
            )

            raw_token = raw_key if raw_key and raw_key != "no-key-required" else ""
            if not raw_token:
                raw_token, _ = resolve_copilot_token()
            if raw_token:
                exchanged = get_copilot_api_token(raw_token)
                self.extra_headers = {
                    **copilot_request_headers(is_agent_turn=True),
                    **getattr(self, "extra_headers", {}),
                }
                return exchanged or raw_token
        except Exception as exc:  # best-effort: headless / unconfigured hosts
            logger.debug("Copilot auth resolution failed: %s", redact_secrets(str(exc)))
        return raw_key or "no-key-required"

    def _refresh_copilot_auth(self) -> bool:
        """Invalidate cached copilot credentials and rebuild the client."""
        if self.provider != "copilot":
            return False
        try:
            from aja.copilot_auth import (
                get_copilot_api_token,
                invalidate_copilot_cache,
                resolve_copilot_token,
            )

            invalidate_copilot_cache()
            raw_token, _ = resolve_copilot_token()
            if raw_token:
                self.api_key = get_copilot_api_token(raw_token) or raw_token
            self._client = None  # fresh client picks up the new token
            return True
        except Exception as exc:  # best-effort
            logger.debug("Copilot token refresh failed: %s", redact_secrets(str(exc)))
            return False

    # ---------------------------------------------------------------- client

    def _get_client(self) -> "AsyncOpenAI":
        """Loop-aware lazy AsyncOpenAI client (recreated across event loops)."""
        if AsyncOpenAI is None:
            raise RuntimeError("The 'openai' package is required for this provider")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if self._client is None or self._client_loop != loop:
            headers = {
                "HTTP-Referer": "https://github.com/aja",
                "X-Title": "AJA Swarm Toolkit",
            }
            headers.update(getattr(self, "extra_headers", {}) or {})
            self._client = AsyncOpenAI(
                api_key=self.api_key or "dummy-local-key",
                base_url=self.base_url,
                default_headers=headers,
                # Explicit per-request timeout (SDK default is 600s) and
                # max_retries=0 so the adapter retry loop is the only layer.
                timeout=_llm_timeout_s(),
                max_retries=0,
            )
            self._client_loop = loop
        return self._client

    # ----------------------------------------------------------------- chat

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
        merged_messages = [
            {"role": "system", "content": system},
            *[
                {
                    "role": m.get("role", "user"),
                    "content": m.get("content", m.get("text", "")),
                    **(
                        {"tool_calls": m["tool_calls"]}
                        if m.get("tool_calls") is not None
                        else {}
                    ),
                    **(
                        {"tool_call_id": m["tool_call_id"]}
                        if m.get("tool_call_id") is not None
                        else {}
                    ),
                }
                for m in (messages or [])
            ],
        ]

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": merged_messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if tools is not None:
            kwargs["tools"] = tools
        if extra_body is not None:
            kwargs["extra_body"] = extra_body

        attempts = max(1, int(retries))
        for attempt in range(attempts):
            try:
                response = await self._get_client().chat.completions.create(**kwargs)
                # Some providers (Copilot Claude passthrough, llama.cpp usage
                # chunks) legally return choices: [] — degrade to an empty
                # LLMResponse instead of IndexError.
                if not getattr(response, "choices", None):
                    logger.warning(
                        "[%s] Provider returned empty choices (model=%s)",
                        self.provider, model,
                    )
                    return LLMResponse(content="", model=model)
                msg = response.choices[0].message
                tool_calls: List[ToolCall] = []
                if getattr(msg, "tool_calls", None):
                    for tc in msg.tool_calls:
                        tool_calls.append(
                            ToolCall(
                                id=tc.id,
                                name=tc.function.name,
                                arguments=tc.function.arguments,
                            )
                        )
                usage: Dict[str, int] = {}
                resp_usage = getattr(response, "usage", None)
                if resp_usage is not None:
                    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        value = getattr(resp_usage, field, None)
                        if isinstance(value, int):
                            usage[field] = value
                return LLMResponse(
                    content=getattr(msg, "content", None) or "",
                    tool_calls=tool_calls,
                    model=model,
                    usage=usage,
                )
            except Exception as e:
                status_code = getattr(e, "status_code", None) or getattr(
                    getattr(e, "response", None), "status_code", None
                )

                if isinstance(status_code, int) and status_code in _NON_RETRYABLE_STATUS:
                    logger.error(
                        "[%s] Non-retryable provider error (%s %s): %s",
                        self.provider, type(e).__name__, status_code,
                        redact_secrets(str(e)),
                    )
                    return LLMResponse(content="", model=model)

                if isinstance(status_code, int) and status_code in (401, 403):
                    if self._refresh_copilot_auth():
                        continue

                logger.warning(
                    "[%s] Error on attempt %d/%d: %s",
                    self.provider, attempt + 1, attempts, redact_secrets(str(e)),
                )
                if attempt == attempts - 1:
                    return LLMResponse(content="", model=model)
                await _backoff_sleep(attempt)

        return LLMResponse(content="", model=model)

    # --------------------------------------------------------------- stream

    async def stream(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: str = "You are a helpful assistant.",
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        merged_messages = [
            {"role": "system", "content": system},
            *[
                {
                    "role": m.get("role", "user"),
                    "content": m.get("content", m.get("text", "")),
                }
                for m in (messages or [])
            ],
        ]
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": merged_messages,
            "stream": True,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        try:
            response_stream = await self._get_client().chat.completions.create(**kwargs)
            async for chunk in response_stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            return
        except Exception as e:
            logger.debug(
                "[%s] stream error, falling back to non-streamed chat: %s",
                self.provider, redact_secrets(str(e)),
            )

        full_res = await self.chat(model=model, messages=messages, system=system, temperature=temperature)
        if full_res and full_res.content:
            yield full_res.content

    # ---------------------------------------------------------------- close

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:  # best-effort teardown
                logger.debug("[%s] client close error: %s", self.provider, exc)
            self._client = None
            self._client_loop = None
