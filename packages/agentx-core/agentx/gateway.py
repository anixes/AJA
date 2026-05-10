import os
import json
from openai import OpenAI
from agentx.orchestration.gateway import UnifiedGateway as OriginalUnifiedGateway, google_api_key, normalize_google_model

class UnifiedGateway(OriginalUnifiedGateway):
    def complete(self, system: str, user: str, model: str = None):
        if model is None:
            try:
                with open("agentx.json", "r") as f:
                    config = json.load(f)
                    model = config.get("swarm_settings", {}).get("models", {}).get("planner", "google:gemini-2.5-flash")
            except Exception:
                model = "google:gemini-2.5-flash"
        
        provider = "openrouter"
        model_name = model
        
        if ":" in model:
            provider, model_name = model.split(":", 1)
        else:
            # Smart fallback
            if "gemini" in model.lower():
                provider = "google"
            elif "gemma" in model.lower() or "llama" in model.lower():
                provider = "llama_cpp"
        
        # Update current state to match the requested provider
        self.provider = provider
        self.base_url = self.PROVIDERS.get(provider, self.PROVIDERS.get("openrouter"))
        
        if self.provider == "google":
            self.base_url = (self.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
            if self.base_url.endswith("/openai"):
                self.base_url = self.base_url[:-7]
            self.api_key = google_api_key(self.api_key)
            model_name = normalize_google_model(model_name)
            self.client = None
        else:
            self.api_key = os.getenv(f"{provider.upper()}_API_KEY") or os.getenv("AI_KEY") or self.api_key
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        return self.chat(model=model_name, prompt=user, system=system) or ""
