import os
import json
import argparse
from pathlib import Path
from openai import OpenAI
from typing import Optional, List, Dict

# Dynamic project root for configuration lookup
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

def load_providers():
    try:
        path = PROJECT_ROOT / "providers.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"openai": "https://api.openai.com/v1"}

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
        self.api_key = api_key or cfg.get("api_key", "")
        self.base_url = base_url or self.PROVIDERS.get(self.provider)
        
        if not self.base_url:
            raise ValueError(f"Unknown provider '{provider}'. Please provide a base_url for custom endpoints.")
            
        self.client = OpenAI(
            api_key=self.api_key, 
            base_url=self.base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/agentx",
                "X-Title": "AgentX Swarm Toolkit"
            }
        )

    def chat(self, model: str, prompt: str, system: str = "You are a helpful assistant."):
        """Simple chat completion."""
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
            
    def embed(self, model: str, text: str) -> list[float]:
        """Generate dense vector embedding for text."""
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
