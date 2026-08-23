"""
Google Gemini provider adapter.

Implements ProviderAdapter against the native generativelanguage REST API
(systemInstruction + contents + functionDeclarations). Header-based auth via
x-goog-api-key keeps the key out of URLs; a query-param fallback is used only
for custom OpenAI-compatible proxy base_urls that require it.
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

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

_MODEL_ALIASES = {
    "gemini-pro": "gemini-2.5-flash",
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-1.5-pro-latest": "gemini-2.5-pro",
    "gemini-1.5-flash": "gemini-2.5-flash",
    "gemini-1.5-flash-latest": "gemini-2.5-flash",
    "gemini-flash-latest": "gemini-2.5-flash",
    "gemini-pro-latest": "gemini-2.5-pro",
}


def _normalize_model(model: str) -> str:
    """Map stale Gemini aliases to currently supported API model ids."""
    model = (model or "gemini-2.5-flash").strip()
    if model.startswith("models/"):
        model = model.split("/", 1)[1]
    return _MODEL_ALIASES.get(model, model)


class GoogleAdapter:
    """ProviderAdapter for Google Gemini (generativelanguage REST API)."""

    provider_name = "google"

    def __init__(
        self,
        provider: str = "google",
        api_key: str = "",
        base_url: Optional[str] = None,
    ):
        self.provider = provider
        self.api_key = api_key or ""
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None

    # -- internals ---------------------------------------------------------

    def _resolve_api_key(self) -> str:
        if not self.api_key:
            self.api_key = (
                os.getenv("GOOGLE_API_KEY")
                or os.getenv("GEMINI_API_KEY")
                or ""
            )
        return self.api_key

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _build_url(self, model: str) -> tuple[str, bool]:
        """Returns (request_url, use_header_auth)."""
        base_url = (self.base_url or _DEFAULT_BASE_URL).rstrip("/")
        if base_url.endswith("/openai"):
            base_url = base_url[:-7]
        url = f"{base_url}/models/{_normalize_model(model)}:generateContent"
        # Header auth by default so the key never appears in the URL (which
        # leaks into proxies/logs/exception messages). Custom proxies may only
        # support query-param auth.
        return url, not self.base_url

    @staticmethod
    def _convert_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        contents = []
        for m in messages:
            role = "user" if m.get("role") == "user" else "model"
            text = m.get("content", m.get("text", ""))
            contents.append({"role": role, "parts": [{"text": text}]})
        return contents

    @staticmethod
    def _convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        declarations = []
        for t in tools:
            fn = t.get("function", t)
            declarations.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
        return [{"functionDeclarations": declarations}]

    @staticmethod
    def _parse_response(
        data: Dict[str, Any], tools_was_provided: bool
    ) -> LLMResponse:
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        content_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for p in parts:
            if "text" in p:
                content_parts.append(p["text"])
            if "functionCall" in p and tools_was_provided:
                fc = p["functionCall"]
                tool_calls.append(
                    ToolCall(
                        id=fc.get("name", ""),
                        name=fc.get("name", ""),
                        arguments=json.dumps(fc.get("args", {})),
                    )
                )
        content = "\n".join(content_parts).strip()
        usage_block = data.get("usageMetadata") or {}
        usage = {}
        for src, dst in (
            ("promptTokenCount", "prompt_tokens"),
            ("candidatesTokenCount", "completion_tokens"),
            ("totalTokenCount", "total_tokens"),
        ):
            if src in usage_block:
                usage[dst] = usage_block[src]
        return LLMResponse(content=content, tool_calls=tool_calls, usage=usage)

    # -- protocol ----------------------------------------------------------

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
        api_key = self._resolve_api_key()
        if not api_key:
            logger.error(
                "[GoogleAdapter] Error: GOOGLE_API_KEY or GEMINI_API_KEY is not configured."
            )
            return LLMResponse()

        url, use_header_auth = self._build_url(model)

        payload: Dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system or "You are a helpful assistant."}]},
            "contents": self._convert_messages(messages),
        }
        if temperature is not None:
            payload["generationConfig"] = {"temperature": temperature}
        if tools is not None:
            payload["tools"] = self._convert_tools(tools)
        if extra_body:
            payload.update(extra_body)

        request_url = url if use_header_auth else f"{url}?key={api_key}"
        headers = {"Content-Type": "application/json"}
        if use_header_auth:
            headers["x-goog-api-key"] = api_key

        try:
            session = self._get_session()
            async with session.post(request_url, json=payload, headers=headers) as response:
                if response.status != 200:
                    detail = await response.text()
                    logger.error(
                        "[GoogleAdapter] Error %s: %s",
                        response.status,
                        redact_secrets(detail[:500]),
                    )
                    return LLMResponse()
                data = await response.json()
                return self._parse_response(data, tools is not None)
        except Exception as e:
            # aiohttp errors embed the full request URL which can carry the
            # API key when query-param auth is in use — always redact.
            logger.error(
                "[GoogleAdapter] request failed: %s: %s",
                type(e).__name__,
                redact_secrets(str(e)),
            )
            return LLMResponse()

    async def stream(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: str = "You are a helpful assistant.",
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        # Native SSE streaming is out of scope for this adapter: yield the
        # full content in one chunk via the non-streaming path.
        result = await self.chat(model=model, messages=messages, system=system, temperature=temperature)
        if result.content:
            yield result.content

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
