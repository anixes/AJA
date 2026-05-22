# agentx/config.py
# ================
# Global configuration and feature flags for AgentX.

import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
from agentx.config_schema import AgentXConfig

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

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

# Load and validate configuration with Pydantic
def load_and_validate_config() -> AgentXConfig:
    config_path = PROJECT_ROOT / "agentx.json"
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return AgentXConfig.model_validate(data)
        except Exception as e:
            logger.error("Configuration validation failed for %s: %s", config_path, e)
            print(f"\n[red]Configuration Validation Error:[/] Malformed config in {config_path}")
            print(f"[red]{e}[/]\n")
            return AgentXConfig()
    return AgentXConfig()

# Loaded configuration object
CONFIG = load_and_validate_config()

# Model Selection
def _get_default_model(key, default):
    try:
        val = getattr(CONFIG.swarm_settings.models, key, default)
        return val if val else default
    except Exception:
        logger.exception("Failed to load model default %s", key)
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
