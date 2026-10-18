"""
libs/aja-core/aja/orchestration/gateway.py - Deprecated Facade over Universal Gateway.

[DEPRECATED] This module is maintained solely for backward compatibility.
New code must import from `aja.gateway` (UnifiedGateway and client_factory).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore

from aja.config import DATA_DIR
from aja.gateway.client_factory import (
    backoff_sleep,
    backoff_sleep_seconds,
    find_project_root,
    get_async_openai_client,
    llm_timeout_s,
)
from aja.runtime.execution.activity import durable_activity
from aja.utils.redact import redact_secrets

logger = logging.getLogger(__name__)

# Legacy re-exports for backward compatibility with existing tests and call sites
_llm_timeout_s = llm_timeout_s
_backoff_sleep_seconds = backoff_sleep_seconds
_backoff_sleep = backoff_sleep
PROJECT_ROOT = find_project_root()


def load_providers() -> Dict[str, str]:
    """Load provider definitions from providers.json, checking standard locations."""
    search_paths = [
        Path.cwd() / "providers.json",
        PROJECT_ROOT / "providers.json",
        Path("providers.json"),
    ]
    for path in search_paths:
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if "google" in data or "openai" in data:
                    return data
        except Exception:
            continue

    return {
        "nvidia": "https://integrate.api.nvidia.com/v1",
        "groq": "https://api.groq.com/openai/v1",
        "together": "https://api.together.xyz/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "openai": "https://api.openai.com/v1",
        "llama_cpp": os.getenv("LLAMA_CPP_URL", "http://localhost:8080/v1"),
        "ollama": os.getenv("OLLAMA_URL", "http://localhost:11434/v1"),
        "google": "https://generativelanguage.googleapis.com/v1beta/openai",
        "copilot": "https://api.githubcopilot.com",
        "gate": "https://gateway.constellationgate.ai/v1",
        "constellation": "https://gateway.constellationgate.ai/v1",
    }


def normalize_google_model(model: str) -> str:
    """Map stale Gemini aliases to currently supported Gemini API model ids."""
    model = (model or "gemini-2.5-flash").strip()
    if model.startswith("models/"):
        model = model.split("/", 1)[1]

    aliases = {
        "gemini-pro": "gemini-2.5-flash",
        "gemini-1.5-pro": "gemini-2.5-pro",
        "gemini-1.5-pro-latest": "gemini-2.5-pro",
        "gemini-1.5-flash": "gemini-2.5-flash",
        "gemini-1.5-flash-latest": "gemini-2.5-flash",
        "gemini-flash-latest": "gemini-2.5-flash",
        "gemini-pro-latest": "gemini-2.5-pro",
    }
    return aliases.get(model, model)


def google_api_key(api_key: str = "") -> str:
    return (
        api_key
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("AI_KEY", "")
    )


def _flatten_google_content(content: Any) -> str:
    """Flatten OpenAI-style content into a single text string for Gemini."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(t for t in texts if t)
    return str(content)


def load_config() -> Dict[str, Any]:
    """Load saved config from .aja/config.json."""
    try:
        cfg_path = DATA_DIR / "config.json"
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _is_claude_model(model: str) -> bool:
    m = (model or "").lower()
    return "claude" in m


def _build_system_message(provider: str, model: str, system: str) -> Dict[str, Any]:
    use_cache = provider == "anthropic" or (
        provider == "copilot" and _is_claude_model(model)
    )
    if use_cache:
        return {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    return {"role": "system", "content": system}


class LLMGateway:
    """[DEPRECATED] Compatibility facade over aja.orchestration.providers and UnifiedGateway."""

    PROVIDERS = load_providers()

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        warnings.warn(
            "aja.orchestration.gateway.LLMGateway is deprecated; use aja.gateway.UnifiedGateway.",
            DeprecationWarning,
            stacklevel=2,
        )
        cfg = load_config()
        self.provider = (provider or cfg.get("provider", "openrouter")).lower()
        self.base_url = (base_url or self.PROVIDERS.get(self.provider, "")).rstrip("/")
        cfg_provider = cfg.get("provider", "").lower()

        if api_key:
            raw_key = api_key
        elif self.provider == cfg_provider:
            raw_key = cfg.get("api_key", "")
        else:
            raw_key = ""

        self.extra_headers = dict(extra_headers or {})

        if self.provider == "google":
            self.api_key = google_api_key(raw_key)
        elif self.provider == "copilot":
            from aja.copilot_auth import (
                copilot_request_headers,
                get_copilot_api_token,
                resolve_copilot_token,
            )

            raw_token = raw_key if (raw_key and raw_key != "no-key-required") else ""
            if not raw_token:
                raw_token, _ = resolve_copilot_token()

            if raw_token:
                exchanged = get_copilot_api_token(raw_token)
                self.api_key = exchanged or raw_token
            else:
                self.api_key = "no-key-required"

            copilot_headers = copilot_request_headers(is_agent_turn=True)
            self.extra_headers = {**copilot_headers, **self.extra_headers}
        elif self.provider == "openai":
            self.api_key = raw_key or os.getenv("OPENAI_API_KEY", "no-key-required")
        elif self.provider == "openrouter":
            self.api_key = raw_key or os.getenv("OPENROUTER_API_KEY", "no-key-required")
        elif self.provider in ("gate", "constellation"):
            self.api_key = (
                raw_key
                or os.getenv("GATE_API_KEY")
                or os.getenv("GATEWAY_KEY")
                or os.getenv("CONSTELLATION_API_KEY", "no-key-required")
            )
        elif not raw_key:
            self.api_key = "no-key-required"
        else:
            self.api_key = raw_key

        self._session: Optional[aiohttp.ClientSession] = None

        if not self.base_url:
            raise ValueError(
                f"Unknown provider '{self.provider}'. Please provide a base_url for custom endpoints."
            )

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=llm_timeout_s())
            )
        return self._session

    def _get_openai_client(self) -> Any:
        return get_async_openai_client(
            base_url=self.base_url,
            api_key=self.api_key,
            extra_headers=self.extra_headers,
            timeout=llm_timeout_s(),
        )

    def _normalize_prompt_to_messages(self, prompt: Any) -> List[Dict[str, Any]]:
        if isinstance(prompt, list):
            return list(prompt)
        elif isinstance(prompt, str):
            return [{"role": "user", "content": prompt}]
        elif isinstance(prompt, dict):
            return [prompt]
        return [{"role": "user", "content": str(prompt)}]

    async def _google_generate_content(
        self,
        model: str,
        prompt: Any,
        system: str = "You are a helpful assistant.",
        temperature: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        session = self._get_session()
        model_name = normalize_google_model(model)
        api_key = google_api_key(self.api_key)
        if not api_key:
            logger.error("[Gateway] Error: GOOGLE_API_KEY or GEMINI_API_KEY is not configured.")
            return None

        base_url = (
            self.base_url or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        if base_url.endswith("/openai"):
            base_url = base_url[:-7]
        url = f"{base_url}/models/{model_name}:generateContent"

        use_header_auth = not self.base_url

        if isinstance(prompt, list):
            contents = []
            for m in prompt:
                role_in = m.get("role")
                role = "user" if role_in in ("user", "tool") else "model"
                text = _flatten_google_content(m.get("content", m.get("text", "")))
                if role_in == "tool":
                    text = f"[Tool result for {m.get('tool_call_id') or 'call'}]: {text}"
                contents.append(
                    {
                        "role": role,
                        "parts": [{"text": text}],
                    }
                )
        else:
            contents = [
                {
                    "role": "user",
                    "parts": [{"text": _flatten_google_content(prompt)}],
                }
            ]

        payload: Dict[str, Any] = {
            "systemInstruction": {
                "parts": [{"text": system or "You are a helpful assistant."}]
            },
            "contents": contents,
        }
        if temperature is not None:
            payload["generationConfig"] = {"temperature": temperature}
        if tools is not None:
            google_tools = []
            for t in tools:
                google_tools.append(
                    {
                        "name": t["function"]["name"],
                        "description": t["function"].get("description", ""),
                        "parameters": t["function"].get("parameters", {}),
                    }
                )
            payload["tools"] = [{"functionDeclarations": google_tools}]

        request_url = url if use_header_auth else f"{url}?key={api_key}"
        headers = {"Content-Type": "application/json"}
        if use_header_auth:
            headers["x-goog-api-key"] = api_key

        try:
            async with session.post(request_url, json=payload, headers=headers) as resp:
                data = await resp.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    return None
                parts = candidates[0].get("content", {}).get("parts") or []
                text_parts = [p.get("text", "") for p in parts if p.get("text")]
                return "\n".join(text_parts).strip() or None
        except Exception as e:
            logger.error("[Gateway Facade] Google request failed: %s", redact_secrets(str(e)))
            return None

    @durable_activity("llm.chat")
    async def chat(
        self,
        model: str,
        prompt: Any,
        system: str = "You are a helpful assistant.",
        retries: int = 3,
        temperature: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if self.provider == "google":
            return await self._google_generate_content(model, prompt, system, temperature, tools)

        if self.provider == "copilot" and (
            not model or model in ("copilot", "github-copilot", "default")
        ):
            model = "gpt-4o-mini"

        use_responses = False
        if self.provider == "copilot":
            if model and model.startswith("copilot:"):
                model = model[8:]
            m_lower = (model or "").lower()
            if "/" in m_lower:
                m_lower = m_lower.rsplit("/", 1)[-1]
            if (
                m_lower.startswith("gpt-")
                and not m_lower.startswith("gpt-5-mini")
                and m_lower not in ("gpt-4o-mini", "gpt-4o", "gpt-4")
            ):
                use_responses = True
            if "claude" in m_lower:
                use_responses = True
            if isinstance(prompt, list) and any(
                isinstance(m.get("content"), list)
                and any(
                    isinstance(p, dict) and p.get("type") == "image_url"
                    for p in m["content"]
                )
                for m in prompt
            ):
                use_responses = True

        if not use_responses:
            try:
                from aja.orchestration.providers import get_adapter_class

                adapter_cls = get_adapter_class(self.provider)
                if adapter_cls is not None:
                    adapter = adapter_cls(api_key=self.api_key, base_url=self.base_url or "")
                    try:
                        llm_resp = await adapter.chat(
                            model=model,
                            messages=self._normalize_prompt_to_messages(prompt),
                            system=system,
                            tools=tools,
                            temperature=temperature,
                            extra_body=extra_body,
                            retries=retries,
                        )
                    finally:
                        close = getattr(adapter, "close", None)
                        if close is not None:
                            try:
                                await close()
                            except Exception:
                                pass
                    if tools is not None:
                        return {
                            "content": llm_resp.content or None,
                            "tool_calls": [
                                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                                for tc in llm_resp.tool_calls
                            ],
                        }
                    return llm_resp.content or None
            except Exception as e:
                logger.debug("[Gateway Facade] Adapter path failed (%s), trying direct client", e)

        # Direct client path with retry loop
        client = None
        if not use_responses:
            client = self._get_openai_client()
            messages = [{"role": "system", "content": system}, *self._normalize_prompt_to_messages(prompt)]
            kwargs: Dict[str, Any] = {"model": model, "messages": messages}
            if temperature is not None:
                kwargs["temperature"] = temperature
            if tools:
                kwargs["tools"] = tools
            if extra_body:
                kwargs.update(extra_body)

        attempt = 0
        while attempt < retries:
            attempt += 1
            try:
                if self.provider == "copilot" and use_responses:
                    input_items = []
                    if isinstance(prompt, list):
                        for m in prompt:
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
                            elif role == "system":
                                input_items.append({"role": "system", "content": [{"type": "input_text", "text": str(content)}]})
                            elif role == "assistant":
                                input_items.append({"role": "assistant", "content": [{"type": "input_text", "text": str(content)}]})
                    else:
                        input_items = [{"role": "user", "content": [{"type": "input_text", "text": str(prompt)}]}]

                    payload = {
                        "model": model,
                        "input": input_items,
                    }
                    if system:
                        payload["instructions"] = system
                    if temperature is not None:
                        payload["temperature"] = float(temperature)
                    if extra_body:
                        payload.update(extra_body)

                    url = f"{(self.base_url or 'https://api.githubcopilot.com').rstrip('/')}/v1/responses"
                    req_headers = {
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    }
                    req_headers.update(self.extra_headers)

                    session = self._get_session()
                    async with session.post(url, json=payload, headers=req_headers) as resp:
                        if resp.status in (401, 403) and attempt < retries:
                            from aja.copilot_auth import (
                                get_copilot_api_token,
                                invalidate_copilot_cache,
                                resolve_copilot_token,
                            )

                            invalidate_copilot_cache()
                            raw_token, _ = resolve_copilot_token()
                            if raw_token:
                                self.api_key = get_copilot_api_token(raw_token) or raw_token
                            continue

                        if resp.status != 200:
                            detail = await resp.text()
                            err = ValueError(
                                f"Copilot Responses API Error {resp.status}: {detail}"
                            )
                            err.status_code = resp.status
                            raise err
                        data = await resp.json()

                    output_items = data.get("output", [])
                    resp_content = ""
                    resp_tool_calls = []
                    for item in output_items:
                        itype = item.get("type")
                        if itype == "message":
                            parts = item.get("content", [])
                            for part in parts:
                                if part.get("type") == "output_text":
                                    resp_content += part.get("text", "")
                        elif itype == "function_call":
                            call_id = item.get("call_id") or f"fc_{item.get('id')}"
                            resp_tool_calls.append(
                                {
                                    "id": call_id,
                                    "name": item.get("name"),
                                    "arguments": item.get("arguments", "{}"),
                                }
                            )

                    if tools is not None:
                        return {"content": resp_content or None, "tool_calls": resp_tool_calls}
                    return resp_content or None

                response = await client.chat.completions.create(**kwargs)
                choices = getattr(response, "choices", None) or []
                if not choices:
                    return None
                choice = choices[0].message
                if tools is not None:
                    tool_calls = []
                    if choice.tool_calls:
                        for tc in choice.tool_calls:
                            tool_calls.append(
                                {
                                    "id": tc.id,
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                }
                            )
                    return {"content": choice.content or None, "tool_calls": tool_calls}
                return choice.content or None
            except Exception as exc:
                status_code = getattr(exc, "status_code", None) or getattr(
                    getattr(exc, "response", None), "status_code", None
                )
                if isinstance(status_code, int) and 400 <= status_code < 500 and status_code not in (401, 403, 429):
                    logger.error(
                        "[Gateway Facade] Non-retryable provider error (%s %s): %s",
                        self.provider, status_code, redact_secrets(str(exc))
                    )
                    return None
                logger.warning(
                    "[Gateway Facade] Error on attempt %d/%d: %s",
                    attempt, retries, redact_secrets(str(exc))
                )
                if attempt >= retries:
                    return None
                if self.provider == "copilot":
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
                        self._openai_client = None
                    except Exception:
                        pass
                await _backoff_sleep(attempt)

        return None

    @durable_activity("llm.embed")
    async def embed(self, model: str, text: str) -> List[float]:
        """Generate dense vector embedding for text."""
        try:
            client = self._get_openai_client()
            response = await client.embeddings.create(input=text, model=model)
            return response.data[0].embedding
        except Exception as e:
            logger.error("[Gateway Facade] Embedding error: %s", redact_secrets(str(e)))
            return []

    async def close(self) -> None:
        """Clean close."""
        if self._session and not self._session.closed:
            await self._session.close()


# Backward-compatible alias
UnifiedGateway = LLMGateway

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLMGateway CLI")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--url")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)

    args = parser.parse_args()
    gateway = LLMGateway(args.provider, args.key, args.url)

    async def main():
        print(f"\n--- Result from {args.provider} ({args.model}) ---")
        result = await gateway.chat(args.model, args.prompt)
        print(result)

    asyncio.run(main())
