# agentx/config.py
# ================
# Global configuration and feature flags for AgentX.

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def find_project_root():
    """Finds the AgentX project root by looking for agentx.json or .git."""
    # Start from this file's location (agentx/config.py)
    current = Path(__file__).resolve().parent
    # Check up to 4 levels up
    for _ in range(4):
        if (current / "agentx.json").exists():
            return current
        if (current / ".git").exists():
            return current
        current = current.parent
    # Fallback to current working directory if nothing found
    return Path(os.getcwd())


PROJECT_ROOT = find_project_root()
AGENTX_DIVERSITY_BETA = True

# Model Selection
def _get_default_model(key, default):
    try:
        config_path = PROJECT_ROOT / "agentx.json"
        if config_path.exists():
            import json
            with open(config_path, "r") as f:
                cfg = json.load(f)
                return cfg.get("swarm_settings", {}).get("models", {}).get(key, default)
    except Exception:
        pass
    return default

AGENTX_PLANNER_MODEL = os.getenv("AGENTX_PLANNER_MODEL", _get_default_model("planner", "google:gemini-2.0-flash"))
AGENTX_WORKER_MODEL = os.getenv("AGENTX_WORKER_MODEL", _get_default_model("worker", "google:gemini-2.0-flash"))

# Telegram Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

# Model API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Local LLM (Llama Gold)
LLAMA_CPP_URL = os.getenv("LLAMA_CPP_URL", "http://localhost:8080/v1")
LLAMA_CPP_API_KEY = os.getenv("LLAMA_CPP_API_KEY", "local-secret")
