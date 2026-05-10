import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from agentx.orchestration.gateway import UnifiedGateway as OriginalUnifiedGateway

load_dotenv()

class UnifiedGateway(OriginalUnifiedGateway):
    def __init__(self, provider: str = None, api_key: str = None, base_url: str = None):
        # Load config to check for offline mode
        self.offline = False
        try:
            with open("agentx.json", "r") as f:
                config = json.load(f)
                self.offline = config.get("swarm_settings", {}).get("offline_mode", False)
        except Exception:
            pass

        if self.offline:
            provider = "llama_cpp"
            api_key = api_key or os.getenv("LLAMA_CPP_API_KEY") or "local-secret"
        
        super().__init__(provider=provider, api_key=api_key, base_url=base_url)

    def chat(self, model: str, prompt: str, system: str = "You are a helpful assistant."):
        """
        Unified chat method that handles local vs remote routing.
        """
        if self.offline:
            # Force local settings
            self.provider = "llama_cpp"
            self.base_url = "http://localhost:8080/v1"
            if not self.client:
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            model = "local-model"

        return super().chat(model=model, prompt=prompt, system=system)

    def complete(self, system: str, user: str, model: str = None):
        """
        Legacy entry point used by some components.
        """
        return self.chat(model=model or "local-model", prompt=user, system=system)

    def summarize(self, content: str, objective: str = "general task") -> str:
        """
        Compresses a large block of text into a high-density summary.
        Used to prevent context bloat during long runs.
        """
        try:
            summary_prompt = (
                f"SYSTEM: You are the Memory Management unit for AgentX.\n"
                f"OBJECTIVE: Compress the following context related to '{objective}' into a high-density summary.\n"
                f"RULES:\n"
                "1. Extract ONLY key decisions, file paths changed, and final status of tasks.\n"
                "2. Ignore redundant logs, greetings, or intermediate thinking.\n"
                "3. Keep the summary under 1,000 tokens.\n\n"
                f"CONTEXT TO SUMMARIZE:\n{content}"
            )
            summary = self.chat(model="local-model", prompt=summary_prompt, system="You are a high-density summarizer.")
            return summary if summary else content
        except Exception as e:
            print(f"[WARNING] Memory summarization failed: {e}. Falling back to raw context.")
            return content
