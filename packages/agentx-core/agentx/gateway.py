
from scripts.core.gateway import UnifiedGateway as OriginalUnifiedGateway
import json

class UnifiedGateway(OriginalUnifiedGateway):
    def complete(self, system: str, user: str, model: str = None):
        if model is None:
            try:
                with open("agentx.json", "r") as f:
                    config = json.load(f)
                    model = config.get("swarm_settings", {}).get("models", {}).get("planner", "google:gemini-3-flash-preview")
            except Exception:
                model = "google:gemini-3-flash-preview"
        
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
        self.api_key = os.getenv(f"{provider.upper()}_API_KEY") or os.getenv("GEMINI_API_KEY") or self.api_key
        
        # Re-initialize client for the specific provider
        from openai import OpenAI
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        return self.chat(model=model_name, prompt=user, system=system) or ""
