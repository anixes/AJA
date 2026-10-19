"""
GitHub Copilot provider adapter.
=================================
Encapsulates all GitHub Copilot specific wire formats, token exchanges,
and endpoint negotiation (/v1/responses vs /v1/chat/completions).

Keeps LLMGateway strictly model-agnostic and provider-agnostic.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from aja.orchestration.providers.base import LLMResponse, ToolCall
from aja.orchestration.providers.openai_compat import OpenAICompatAdapter
from aja.utils.redact import redact_secrets

logger = logging.getLogger(__name__)


class CopilotAdapter(OpenAICompatAdapter):
    """
    ProviderAdapter specialized for GitHub Copilot.
    Handles device token exchange, token rotation with concurrency locks,
    and protocol translation between Chat Completions and Responses endpoints.
    """

    def __init__(
        self,
        provider: str = "copilot",
        api_key: str = "",
        base_url: str = "",
        session_getter: Any = None,
        **kwargs,
    ):
        super().__init__(provider="copilot", api_key=api_key, base_url=base_url, **kwargs)
        self._refresh_lock = asyncio.Lock()
        self._session_getter = session_getter
        self._session: Optional[aiohttp.ClientSession] = None

    def _normalize_copilot_model(self, model: Optional[str]) -> str:
        """Map user-facing aliases to canonical models supported by GitHub Copilot."""
        if not model or model.lower() in ("copilot", "github-copilot", "default"):
            return "gpt-4o"
        if model.lower() in ("copilot-mini", "copilot-fast", "mini"):
            return "gpt-4o-mini"
        if model.startswith("copilot:"):
            return model[8:]
        return model

    def _should_use_responses_api(self, model: str, messages: List[Dict[str, Any]]) -> bool:
        """Determine whether the model/payload requires the GitHub Copilot /v1/responses preview endpoint."""
        m_lower = (model or "").lower()
        if "/" in m_lower:
            m_lower = m_lower.rsplit("/", 1)[-1]

        if "claude" in m_lower:
            return True

        if (
            m_lower.startswith("gpt-")
            and not m_lower.startswith("gpt-5-mini")
            and m_lower not in ("gpt-4o-mini", "gpt-4o", "gpt-4")
        ):
            return True

        # Multimodal payloads with image parts require responses API
        if messages and any(
            isinstance(m.get("content"), list)
            and any(
                isinstance(p, dict) and p.get("type") == "image_url"
                for p in m["content"]
            )
            for m in messages
        ):
            return True

        return False

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._session_getter is not None:
            sess = self._session_getter()
            if asyncio.iscoroutine(sess):
                return await sess
            return sess
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
        return self._session

    async def _refresh_auth_locked(self) -> bool:
        async with self._refresh_lock:
            return self._refresh_copilot_auth()

    async def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: str = "You are a helpful assistant.",
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        retries: int = 2,
    ) -> LLMResponse:
        canonical_model = self._normalize_copilot_model(model)

        if not self._should_use_responses_api(canonical_model, messages):
            # Standard OpenAI-compatible path
            return await super().chat(
                model=canonical_model,
                messages=messages,
                system=system,
                tools=tools,
                temperature=temperature,
                extra_body=extra_body,
                retries=retries,
            )

        # Copilot Responses preview API path
        return await self._chat_responses_api(
            model=canonical_model,
            messages=messages,
            system=system,
            tools=tools,
            temperature=temperature,
            extra_body=extra_body,
            retries=retries,
        )

    async def _chat_responses_api(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]],
        temperature: Optional[float],
        extra_body: Optional[Dict[str, Any]],
        retries: int,
    ) -> LLMResponse:
        input_items = []
        for m in (messages or []):
            role = m.get("role", "user")
            content = m.get("content", m.get("text", ""))
            if role == "user":
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if isinstance(part, str):
                            parts.append({"type": "input_text", "text": part})
                        elif isinstance(part, dict):
                            ptype = part.get("type")
                            if ptype == "text":
                                parts.append({"type": "input_text", "text": part.get("text", "")})
                            elif ptype == "image_url":
                                img_url = (
                                    part.get("image_url", {}).get("url")
                                    if isinstance(part.get("image_url"), dict)
                                    else part.get("image_url")
                                )
                                parts.append({"type": "input_image", "image_url": img_url})
                    input_items.append({"role": "user", "content": parts})
                else:
                    input_items.append({"role": "user", "content": [{"type": "input_text", "text": str(content)}]})
            elif role in ("system", "assistant"):
                input_items.append({"role": role, "content": [{"type": "input_text", "text": str(content)}]})

        payload: Dict[str, Any] = {
            "model": model,
            "input": input_items,
        }
        if system:
            payload["instructions"] = system
        if temperature is not None:
            payload["temperature"] = float(temperature)
        if extra_body:
            payload.update(extra_body)

        base = (self.base_url or "https://api.githubcopilot.com").rstrip("/")
        url = f"{base}/v1/responses"
        session = await self._get_http_session()

        attempts = max(1, int(retries))
        for attempt in range(attempts):
            req_headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                **getattr(self, "extra_headers", {}),
            }

            try:
                async with session.post(url, json=payload, headers=req_headers) as resp:
                    if resp.status in (401, 403) and attempt < attempts - 1:
                        logger.info("[CopilotAdapter] Token expired on Responses API; refreshing...")
                        if await self._refresh_auth_locked():
                            continue

                    if resp.status != 200:
                        detail = await resp.text()
                        logger.error(
                            "[CopilotAdapter] Responses API error HTTP %d: %s",
                            resp.status, redact_secrets(detail),
                        )
                        err = RuntimeError(f"Copilot Responses API error HTTP {resp.status}: {redact_secrets(detail)}")
                        setattr(err, "status_code", resp.status)
                        raise err

                    data = await resp.json()

                output_items = data.get("output", [])
                resp_content = ""
                resp_tool_calls: List[ToolCall] = []

                for item in output_items:
                    itype = item.get("type")
                    if itype == "message":
                        for part in item.get("content", []):
                            if part.get("type") == "output_text":
                                resp_content += part.get("text", "")
                    elif itype == "function_call":
                        call_id = item.get("call_id") or f"fc_{item.get('id', '')}"
                        resp_tool_calls.append(
                            ToolCall(
                                id=call_id,
                                name=item.get("name", ""),
                                arguments=item.get("arguments", "{}"),
                            )
                        )

                return LLMResponse(
                    content=resp_content,
                    tool_calls=resp_tool_calls,
                    model=model,
                )
            except Exception as e:
                if hasattr(e, "status_code") and e.status_code not in (401, 403, 429):
                    raise
                logger.warning(
                    "[CopilotAdapter] Error on attempt %d/%d: %s",
                    attempt + 1, attempts, redact_secrets(str(e)),
                )
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(1.0 * (attempt + 1))

        raise RuntimeError(f"Copilot Responses API failed after {attempts} attempts")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        await super().close()
