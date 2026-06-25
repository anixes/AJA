from aja.config import DATA_DIR
import os
import json
import argparse
import asyncio
import aiohttp
from pathlib import Path
from openai import AsyncOpenAI
from typing import Optional, Any, List, Dict


def find_project_root() -> Path:
    """Find the repo root from CWD or this module location."""
    candidates = [Path.cwd(), *Path(__file__).resolve().parents]
    for current in candidates:
        if (current / "aja.json").exists() or (current / ".git").exists():
            return current
    return Path.cwd()


# Dynamic project root for configuration lookup
PROJECT_ROOT = find_project_root()


def load_providers():
    """Load provider definitions from providers.json, checking multiple possible locations."""
    search_paths = [
        Path.cwd()
        / "providers.json",  # Current working directory (usually project root)
        PROJECT_ROOT / "providers.json",  # Project root
        Path("providers.json"),  # Literal local
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
        "openai": "https://api.openai.com/v1",
        "google": "https://generativelanguage.googleapis.com/v1beta",
        "openrouter": "https://openrouter.ai/api/v1",
        "llama_cpp": "http://localhost:8080/v1",
        "copilot": "https://api.githubcopilot.com"
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


def load_config():
    """Load saved config from .aja/config.json."""
    try:
        cfg_path = DATA_DIR / "config.json"
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _is_claude_model(model: str) -> bool:
    """Return True if the model name identifies a Claude variant."""
    m = (model or "").lower()
    return "claude" in m


def _build_system_message(provider: str, model: str, system: str) -> dict:
    """
    Build the system message dict for the messages array.

    For Anthropic-compatible providers (``anthropic`` or ``copilot`` with a
    Claude model) we annotate the system content block with
    ``cache_control: {"type": "ephemeral"}`` so the API can cache the static
    system prefix across turns in a DirectSession, reducing TTFT and cost.

    All other providers receive a plain ``{"role": "system", "content": ...}``
    dict which is universally compatible with the OpenAI Chat Completions API.
    """
    use_cache = provider == "anthropic" or (
        provider == "copilot" and _is_claude_model(model)
    )

    if use_cache:
        # Anthropic cache_control annotation — works for both direct Anthropic
        # API and Copilot-routed Claude models that honour the field.
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

    # Standard system message for all other providers (OpenAI, Google via
    # OpenAI compat, OpenRouter, llama_cpp, etc.)
    return {"role": "system", "content": system}


class LLMGateway:
    """
    LLMGateway — the low-level AI provider client for AJA.
    Supports NVIDIA, Groq, Together, OpenRouter, Google (Gemini), and custom (BYO) endpoints.
    Reads config from .aja/config.json first, then falls back to constructor args.
    """

    PROVIDERS = load_providers()

    def __init__(
        self, provider: str = None, api_key: str = None, base_url: Optional[str] = None, extra_headers: Optional[Dict[str, str]] = None
    ):
        cfg = load_config()
        self.provider = (provider or cfg.get("provider", "openrouter")).lower()
        self.base_url = base_url or self.PROVIDERS.get(self.provider, "")
        cfg_provider = cfg.get("provider", "").lower()
        if api_key:
            raw_key = api_key
        elif self.provider == cfg_provider:
            raw_key = cfg.get("api_key", "")
        else:
            raw_key = ""

        if self.provider == "google":
            self.api_key = google_api_key(raw_key)
        else:
            self.api_key = raw_key

        self.extra_headers = extra_headers or {}

        # Handle Copilot Token Exchange and Header Construction
        if self.provider == "copilot":
            from aja.copilot_auth import resolve_copilot_token, get_copilot_api_token, copilot_request_headers
            if not self.api_key:
                raw_token, _ = resolve_copilot_token()
            else:
                raw_token = self.api_key
            
            if raw_token:
                self.api_key = get_copilot_api_token(raw_token)
            
            copilot_headers = copilot_request_headers(is_agent_turn=True)
            self.extra_headers = {**copilot_headers, **self.extra_headers}

        # Ensure copilot defaults to its specific api base URL if not in PROVIDERS
        default_url = "https://api.githubcopilot.com" if self.provider == "copilot" else self.PROVIDERS.get(self.provider)
        self.base_url = base_url or default_url

        if not self.base_url:
            raise ValueError(
                f"Unknown provider '{self.provider}'. Please provide a base_url for custom endpoints."
            )

    async def complete(self, system: str, user: str, model: str = None, retries: int = 3, temperature: Optional[float] = None):
        """
        Convenience method for deterministic completions.
        """
        if model is None:
            model = "gemini-2.5-flash"

        return await self.chat(model=model, prompt=user, system=system, retries=retries, temperature=temperature)

    from aja.runtime.execution.activity import durable_activity

    @durable_activity("llm.chat")
    async def chat(
        self, model: str, prompt: Any, system: str = "You are a helpful assistant.", retries: int = 3, temperature: Optional[float] = None, tools: Optional[List[Dict[str, Any]]] = None, extra_body: Optional[Dict[str, Any]] = None
    ):
        if self.provider == "copilot" and model in ("copilot", "github-copilot", "default"):
            model = "gpt-4o-mini"

        for attempt in range(1, retries + 1):
            try:
                if self.provider == "google":
                    return await self._google_generate_content(model, prompt, system, temperature, tools)

                use_responses = False
                
                # Clean up model name for Copilot
                if self.provider == "copilot":
                    if model.startswith("copilot:"):
                        model = model[8:]
                    
                    m_lower = model.lower()
                    if "/" in m_lower:
                        m_lower = m_lower.rsplit("/", 1)[-1]
                    if m_lower.startswith("gpt-") and not m_lower.startswith("gpt-5-mini") and m_lower not in ("gpt-4o-mini", "gpt-4o", "gpt-4"):
                        use_responses = True
                    # Claude 3.5 Sonnet needs the responses API for Copilot currently.
                    if "claude" in m_lower:
                        use_responses = True

                if self.provider == "copilot" and use_responses:
                    # Build Responses API input parts
                    input_items = []
                    
                    # Convert prompt (which can be a string or a list of messages)
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
                                                img_url = part.get("image_url", {}).get("url") if isinstance(part.get("image_url"), dict) else part.get("image_url")
                                                parts.append({"type": "input_image", "image_url": img_url})
                                    input_items.append({"role": "user", "content": parts})
                                else:
                                    input_items.append({"role": "user", "content": str(content)})
                            elif role == "assistant":
                                tcs = m.get("tool_calls")
                                if isinstance(content, list):
                                    parts = [{"type": "output_text", "text": p.get("text", "")} for p in content if isinstance(p, dict) and p.get("type") == "text"]
                                    input_items.append({"role": "assistant", "content": parts})
                                else:
                                    input_items.append({"role": "assistant", "content": str(content)})
                                if tcs:
                                    for tc in tcs:
                                        fn = tc.get("function", {})
                                        input_items.append({
                                            "type": "function_call",
                                            "call_id": tc.get("id") or tc.get("call_id") or f"call_{len(input_items)}",
                                            "name": fn.get("name"),
                                            "arguments": fn.get("arguments", "{}")
                                        })
                            elif role == "tool":
                                call_id = m.get("tool_call_id")
                                input_items.append({
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": str(content)
                                })
                    else:
                        input_items.append({"role": "user", "content": str(prompt)})

                    response_tools = []
                    if tools:
                        for item in tools:
                            fn = item.get("function", {}) if isinstance(item, dict) else {}
                            name = fn.get("name")
                            if name:
                                response_tools.append({
                                    "type": "function",
                                    "name": name,
                                    "description": fn.get("description", ""),
                                    "strict": False,
                                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                                })

                    payload = {
                        "model": model,
                        "instructions": system,
                        "input": input_items,
                        "store": False
                    }
                    if response_tools:
                        payload["tools"] = response_tools
                        payload["tool_choice"] = "auto"
                        payload["parallel_tool_calls"] = True

                    if temperature is not None:
                        payload["temperature"] = float(temperature)
                    if extra_body:
                        payload.update(extra_body)

                    url = f"{self.base_url.rstrip('/')}/v1/responses"
                    req_headers = {
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    }
                    req_headers.update(self.extra_headers)

                    timeout = aiohttp.ClientTimeout(total=60)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(url, json=payload, headers=req_headers) as resp:
                            if resp.status != 200:
                                detail = await resp.text()
                                raise ValueError(f"Copilot Responses API Error {resp.status}: {detail}")
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
                            resp_tool_calls.append({
                                "id": call_id,
                                "name": item.get("name"),
                                "arguments": item.get("arguments", "{}")
                            })

                    if tools is not None:
                        return {"content": resp_content, "tool_calls": resp_tool_calls}
                    return resp_content

                if isinstance(prompt, list):
                    prompt_messages = []
                    for m in prompt:
                        msg_dict = {
                            "role": m.get("role", "user"),
                            "content": m.get("content", m.get("text", ""))
                        }
                        if m.get("tool_calls") is not None:
                            msg_dict["tool_calls"] = m.get("tool_calls")
                        if m.get("tool_call_id") is not None:
                            msg_dict["tool_call_id"] = m.get("tool_call_id")
                        prompt_messages.append(msg_dict)
                    messages = [_build_system_message(self.provider, model, system)] + prompt_messages
                else:
                    messages = [
                        _build_system_message(self.provider, model, system),
                        {"role": "user", "content": prompt},
                    ]

                kwargs = {
                    "model": model,
                    "messages": messages,
                }
                if temperature is not None:
                    kwargs["temperature"] = temperature
                if tools is not None:
                    kwargs["tools"] = tools
                if extra_body is not None:
                    kwargs["extra_body"] = extra_body

                headers = {
                    "HTTP-Referer": "https://github.com/aja",
                    "X-Title": "AJA Swarm Toolkit",
                }
                headers.update(self.extra_headers)

                async with AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    default_headers=headers,
                ) as client:
                    response = await client.chat.completions.create(**kwargs)
                
                msg = response.choices[0].message
                if tools is not None:
                    tool_calls = []
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            tool_calls.append({
                                "id": tc.id,
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            })
                    return {"content": msg.content or "", "tool_calls": tool_calls}
                return msg.content or ""
            except Exception as e:
                print(f"[Gateway] Error on attempt {attempt}: {e}")
                if attempt == retries:
                    return None
                await asyncio.sleep(2 ** attempt)

    async def _google_generate_content(self, model: str, prompt: Any, system: str, temperature: Optional[float] = None, tools: Optional[List[Dict[str, Any]]] = None):
        model_name = normalize_google_model(model)
        api_key = google_api_key(self.api_key)
        if not api_key:
            print("[Gateway] Error: GOOGLE_API_KEY or GEMINI_API_KEY is not configured.")
            return None

        base_url = (self.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        if base_url.endswith("/openai"):
            base_url = base_url[:-7]
        url = f"{base_url}/models/{model_name}:generateContent?key={api_key}"
        
        if isinstance(prompt, list):
            contents = []
            for m in prompt:
                role = "user" if m.get("role") == "user" else "model"
                text = m.get("content", m.get("text", ""))
                contents.append({
                    "role": role,
                    "parts": [{"text": text}],
                })
        else:
            contents = [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ]

        payload = {
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
                google_tools.append({
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get("parameters", {})
                })
            payload["tools"] = [{"functionDeclarations": google_tools}]

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as response:
                    if response.status != 200:
                        detail = await response.text()
                        print(f"[Gateway] Google Error {response.status}: {detail}")
                        return None
                    
                    data = await response.json()
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if tools is not None:
                        content = ""
                        tool_calls = []
                        import json
                        for p in parts:
                            if "text" in p:
                                content += p["text"]
                            if "functionCall" in p:
                                fc = p["functionCall"]
                                tool_calls.append({
                                    "id": fc.get("name"),
                                    "name": fc.get("name"),
                                    "arguments": json.dumps(fc.get("args", {}))
                                })
                        return {"content": content.strip(), "tool_calls": tool_calls}
                    else:
                        text_parts = [part.get("text", "") for part in parts if part.get("text")]
                        return "\n".join(text_parts).strip() or None
            except Exception as e:
                print(f"[Gateway] Google Error: {e}")
                return None

    @durable_activity("llm.embed")
    async def embed(self, model: str, text: str) -> list[float]:
        """Generate dense vector embedding for text."""
        if self.provider == "google":
            print("[Gateway] Embedding Error: native Google embeddings are not wired yet.")
            return []

        try:
            headers = {
                "HTTP-Referer": "https://github.com/aja",
                "X-Title": "AJA Swarm Toolkit",
            }
            headers.update(self.extra_headers)
            
            async with AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                default_headers=headers,
            ) as client:
                response = await client.embeddings.create(input=text, model=model)
                return response.data[0].embedding
        except Exception as e:
            print(f"[Gateway] Embedding Error: {e}")
            return []


# Backward-compatible alias — remove after all call-sites are updated to LLMGateway
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

