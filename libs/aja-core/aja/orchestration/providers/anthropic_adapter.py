"""
Native Anthropic Messages API adapter (POST /v1/messages).

Uses the native wire format — not the OpenAI-compat layer — so prompt
caching (cache_control breakpoints) and extended thinking remain available.

Adding this adapter = implementing ProviderAdapter from base.py.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from aja.orchestration.providers.base import LLMResponse, ToolCall
from aja.utils.redact import redact_secrets

logger = logging.getLogger(__name__)

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


class AnthropicAdapter:
    """ProviderAdapter for Anthropic via the native Messages API."""

    provider_name = "anthropic"

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------ #
    # HTTP plumbing
    # ------------------------------------------------------------------ #

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                }
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------ #
    # Request translation (common currency -> Anthropic wire format)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _translate_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """OpenAI tool schema -> Anthropic {name, description, input_schema}."""
        translated: List[Dict[str, Any]] = []
        for tool in tools:
            fn = tool.get("function", tool)
            translated.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return translated

    @staticmethod
    def _build_body(
        model: str,
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]],
        temperature: Optional[float],
        extra_body: Optional[Dict[str, Any]],
        max_tokens: int,
    ) -> Dict[str, Any]:
        system_parts: List[str] = []
        chat_messages: List[Dict[str, Any]] = []

        for message in messages:
            if message.get("role") == "system":
                content = message.get("content") or ""
                if isinstance(content, str):
                    system_parts.append(content)
                else:
                    for block in content:
                        text = block.get("text", "") if isinstance(block, dict) else str(block)
                        if text:
                            system_parts.append(text)
            else:
                chat_messages.append(message)

        if system and (not system_parts or system != "You are a helpful assistant."):
            system_parts.insert(0, system)

        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
        }

        if system_parts:
            blocks: List[Dict[str, Any]] = [
                {"type": "text", "text": part} for part in system_parts if part
            ]
            if blocks:
                # Prompt-caching breakpoint on the last system block.
                blocks[-1]["cache_control"] = {"type": "ephemeral"}
                body["system"] = blocks

        if tools:
            body["tools"] = AnthropicAdapter._translate_tools(tools)

        if temperature is not None:
            body["temperature"] = temperature

        if extra_body:
            overrides = dict(extra_body)
            max_tokens_override = overrides.pop("max_tokens", None)
            if max_tokens_override is not None:
                body["max_tokens"] = int(max_tokens_override)
            body.update(overrides)

        return body

    # ------------------------------------------------------------------ #
    # Response translation (Anthropic content blocks -> LLMResponse)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_response(payload: Dict[str, Any]) -> LLMResponse:
        content_parts: List[str] = []
        tool_calls: List[ToolCall] = []

        for block in payload.get("content", []):
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text", "")
                if text:
                    content_parts.append(text)
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=json.dumps(block.get("input", {})),
                    )
                )

        usage_raw = payload.get("usage", {}) or {}
        usage = {
            "input_tokens": int(usage_raw.get("input_tokens", 0)),
            "output_tokens": int(usage_raw.get("output_tokens", 0)),
        }

        return LLMResponse(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            model=payload.get("model", ""),
            usage=usage,
        )

    # ------------------------------------------------------------------ #
    # Protocol surface
    # ------------------------------------------------------------------ #

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
        body = self._build_body(
            model=model,
            messages=messages,
            system=system,
            tools=tools,
            temperature=temperature,
            extra_body=extra_body,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        session = self._get_session()
        last_error: Optional[Exception] = None

        for attempt in range(max(1, retries)):
            try:
                async with session.post(
                    f"{ANTHROPIC_BASE_URL}/v1/messages", json=body
                ) as resp:
                    payload = await resp.json(content_type=None)
                    if resp.status >= 400:
                        detail = redact_secrets(json.dumps(payload)[:500])
                        raise RuntimeError(
                            f"anthropic api error {resp.status}: {detail}"
                        )
                    return self._parse_response(payload)
            except Exception as exc:  # noqa: BLE001 — retried below, logged redacted
                last_error = exc
                logger.warning(
                    "anthropic chat attempt %d/%d failed: %s",
                    attempt + 1,
                    retries,
                    redact_secrets(exc),
                )

        raise RuntimeError(f"anthropic chat failed after {retries} attempt(s): {redact_secrets(last_error)}")

    async def stream(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: str = "You are a helpful assistant.",
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        response = await self.chat(model, messages, system=system, temperature=temperature)
        yield response.content
