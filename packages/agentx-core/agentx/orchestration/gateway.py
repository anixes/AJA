import os
import json
import argparse
import urllib.error
import urllib.request
from pathlib import Path
from openai import OpenAI
from typing import Optional

def find_project_root() -> Path:
    """Find the repo root from CWD or this module location."""
    candidates = [Path.cwd(), *Path(__file__).resolve().parents]
    for current in candidates:
        if (current / "agent.json").exists() or (current / ".git").exists():
            return current
    return Path.cwd()

# Dynamic project root for configuration lookup
PROJECT_ROOT = find_project_root()

def load_providers():
    """Load provider definitions from providers.json, checking multiple possible locations."""
    search_paths = [
        Path.cwd() / "providers.json",           # Current working directory (usually project root)
        PROJECT_ROOT / "providers.json",         # Project root
        Path("providers.json")                   # Literal local
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
        "llama_cpp": "http://localhost:8080/v1"
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
    return api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("AI_KEY", "")

def load_config():
    """Load saved config from .agentx/config.json."""
    try:
        cfg_path = PROJECT_ROOT / ".agentx" / "config.json"
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

class UnifiedGateway:
    """
    A unified client for multiple AI model providers.
    Supports NVIDIA, Groq, Together, OpenRouter, and Custom (BYO) endpoints.
    Reads config from .agentx/config.json first, then falls back to constructor args.
    """
    
    PROVIDERS = load_providers()

    def __init__(self, provider: str = None, api_key: str = None, base_url: Optional[str] = None):
        cfg = load_config()
        self.provider = (provider or cfg.get("provider", "openrouter")).lower()
        self.api_key = google_api_key(api_key or cfg.get("api_key", "")) if self.provider == "google" else api_key or cfg.get("api_key", "")
        self.base_url = base_url or self.PROVIDERS.get(self.provider)
        
        if not self.base_url:
            raise ValueError(f"Unknown provider '{provider}'. Please provide a base_url for custom endpoints.")

        self.client = None
        if self.provider != "google":
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                default_headers={
                    "HTTP-Referer": "https://github.com/agent",
                    "X-Title": "Agent Swarm Toolkit"
                }
            )

    def chat(self, model: str, prompt: str, system: str = "You are a helpful assistant."):
        """Simple chat completion."""
        if self.provider == "google":
            return self._google_generate_content(model, prompt, system)

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[Gateway] Error: {e}")
            return None

    def _google_generate_content(self, model: str, prompt: str, system: str):
        model_name = normalize_google_model(model)
        api_key = google_api_key(self.api_key)
        if not api_key:
            print("[Gateway] Error: GOOGLE_API_KEY or GEMINI_API_KEY is not configured.")
            return None

        base_url = (self.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        if base_url.endswith("/openai"):
            base_url = base_url[:-7]
        url = f"{base_url}/models/{model_name}:generateContent"
        payload = {
            "systemInstruction": {
                "parts": [{"text": system or "You are a helpful assistant."}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            print(f"[Gateway] Google Error {e.code}: {detail}")
            return None
        except Exception as e:
            print(f"[Gateway] Google Error: {e}")
            return None

        parts = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        text_parts = [part.get("text", "") for part in parts if part.get("text")]
        return "\n".join(text_parts).strip() or None
            
    def embed(self, model: str, text: str) -> list[float]:
        """Generate dense vector embedding for text."""
        if self.provider == "google":
            print("[Gateway] Embedding Error: native Google embeddings are not wired yet.")
            return []

        try:
            # We assume the configured provider supports /embeddings
            response = self.client.embeddings.create(
                input=text,
                model=model
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"[Gateway] Embedding Error: {e}")
            return []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unified AI API Gateway CLI")
    parser.add_argument("--provider", required=True, help="Provider name (nvidia, groq, together, openrouter, custom)")
    parser.add_argument("--key", required=True, help="API Key")
    parser.add_argument("--url", help="Custom base URL (required if provider is 'custom')")
    parser.add_argument("--model", required=True, help="Model string (e.g. nvidia/llama-3.1-nemotron-70b-instruct)")
    parser.add_argument("--prompt", required=True, help="User prompt")
    
    args = parser.parse_args()
    
    gateway = UnifiedGateway(args.provider, args.key, args.url)
    print(f"\n--- Result from {args.provider} ({args.model}) ---")
    print(gateway.chat(args.model, args.prompt))
