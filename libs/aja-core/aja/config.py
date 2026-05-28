# aja/config.py
# ================
# Global configuration and feature flags for AJA.

import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
from aja.config_schema import AJAConfig

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

import importlib.resources
import filelock

def find_project_root():
    """
    Finds the AJA project root.

    Priority:
    1. Traverse up from __file__ looking for aja.json or .git (source-tree / dev mode).
    2. Fall back to the platform data directory (installed-wheel context).
    """
    current = Path(__file__).resolve().parent
    for _ in range(6):
        if (current / "aja.json").exists() or (current / ".git").exists():
            return current
        current = current.parent
    # Installed-wheel fallback: use the user data directory as the project root
    return Path(platformdirs.user_data_dir("AJA", "Anixes"))


import platformdirs
import shutil

PROJECT_ROOT = find_project_root()
AJA_DIVERSITY_BETA = True

def _get_data_dir() -> Path:
    env_dir = os.getenv("AJA_DATA_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    
    # Check for legacy local .aja folder for backward compatibility
    legacy_dir = PROJECT_ROOT / ".aja"
    new_dir = Path(platformdirs.user_data_dir("AJA", "Anixes"))
    
    if legacy_dir.exists() and legacy_dir.is_dir() and not new_dir.exists():
        lock_path = new_dir.parent / ".aja_migration.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with filelock.FileLock(str(lock_path), timeout=10):
                # Re-check inside the lock to handle parallel worker races
                if legacy_dir.exists() and not new_dir.exists():
                    logger.info("Migrating legacy data directory from %s to %s", legacy_dir, new_dir)
                    new_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(legacy_dir), str(new_dir))
        except Exception as e:
            logger.error("Failed to migrate legacy directory: %s", e)
            return legacy_dir
            
    return new_dir

DATA_DIR = _get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Load and validate configuration with Pydantic
def load_and_validate_config() -> AJAConfig:
    config_path = DATA_DIR / "aja.json"
    if not config_path.exists() and (PROJECT_ROOT / "aja.json").exists():
        config_path = PROJECT_ROOT / "aja.json"

    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return AJAConfig.model_validate(data)
        except Exception as e:
            logger.error("Configuration validation failed for %s: %s", config_path, e)
            try:
                from rich import print as rprint
                rprint(f"\n[bold red]Configuration Validation Error:[/] Malformed config in {config_path}")
                rprint(f"[bold red]{e}[/]\n")
            except ImportError:
                print(f"\nConfiguration Validation Error: Malformed config in {config_path}")
                print(f"{e}\n")
            return AJAConfig()
    return AJAConfig()

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

AJA_PLANNER_MODEL = os.getenv("AJA_PLANNER_MODEL", _get_default_model("planner", "google:gemini-2.0-flash"))
AJA_WORKER_MODEL = os.getenv("AJA_WORKER_MODEL", _get_default_model("worker", "google:gemini-2.0-flash"))

# Telegram Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

# Model API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Local LLM (Llama Gold)
LLAMA_CPP_URL = os.getenv("LLAMA_CPP_URL", "http://localhost:8080/v1")
LLAMA_CPP_API_KEY = os.getenv("LLAMA_CPP_API_KEY", "local-secret")
