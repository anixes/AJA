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


class LLMGateway:
    """
    LLMGateway — the low-level AI provider client for AJA.
    Supports NVIDIA, Groq, Together, OpenRouter, Google (Gemini), and custom (BYO) endpoints.
    Reads config from .aja/config.json first, then falls back to constructor args.
    """

    PROVIDERS = load_providers()

    def __init__(
        self, provider: str = None, api_key: str = None, base_url: Optional[str] = None
    ):
        cfg = load_config()
        self.provider = (provider or cfg.get("provider", "openrouter")).lower()
        self.api_key = (
            google_api_key(api_key or cfg.get("api_key", ""))
            if self.provider == "google"
            else api_key or cfg.get("api_key", "")
        )
        self.base_url = base_url or self.PROVIDERS.get(self.provider)

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
        self, model: str, prompt: Any, system: str = "You are a helpful assistant.", retries: int = 3, temperature: Optional[float] = None
    ):
        """Simple chat completion with backoff retries."""
        for attempt in range(1, retries + 1):
            try:
                if self.provider == "google":
                    return await self._google_generate_content(model, prompt, system, temperature)

                if isinstance(prompt, list):
                    prompt_messages = []
                    for m in prompt:
                        prompt_messages.append({
                            "role": m.get("role", "user"),
                            "content": m.get("content", m.get("text", ""))
                        })
                    messages = [{"role": "system", "content": system}] + prompt_messages
                else:
                    messages = [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ]

                kwargs = {
                    "model": model,
                    "messages": messages,
                }
                if temperature is not None:
                    kwargs["temperature"] = temperature

                async with AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    default_headers={
                        "HTTP-Referer": "https://github.com/aja",
                        "X-Title": "AJA Swarm Toolkit",
                    },
                ) as client:
                    response = await client.chat.completions.create(**kwargs)
                return response.choices[0].message.content
            except Exception as e:
                print(f"[Gateway] Error on attempt {attempt}: {e}")
                if attempt == retries:
                    return None
                await asyncio.sleep(2 ** attempt)

    async def _google_generate_content(self, model: str, prompt: Any, system: str, temperature: Optional[float] = None):
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

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as response:
                    if response.status != 200:
                        detail = await response.text()
                        print(f"[Gateway] Google Error {response.status}: {detail}")
                        return None
                    
                    data = await response.json()
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
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
            async with AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                default_headers={
                    "HTTP-Referer": "https://github.com/aja",
                    "X-Title": "AJA Swarm Toolkit",
                },
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

