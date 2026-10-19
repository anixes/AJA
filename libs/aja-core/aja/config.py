# aja/config.py
# ================
# Global configuration and feature flags for AJA.

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import filelock
import platformdirs

from aja.config_schema import AJAConfig

# Load environment variables from .env file (override stale OS variables)
load_dotenv(override=True)

# Normalize Telegram env aliases across components
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
if bot_token:
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", bot_token)
    os.environ.setdefault("TELEGRAM_TOKEN", bot_token)

# One-way sync: ALLOWED_USER_ID can default destination CHAT_ID,
# but an untrusted notification CHAT_ID must NEVER grant authorization into ALLOWED_USER_ID!
allowed_user = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
if allowed_user and not os.environ.get("TELEGRAM_CHAT_ID"):
    primary_chat = allowed_user.split(",")[0].strip()
    os.environ.setdefault("TELEGRAM_CHAT_ID", primary_chat)

logger = logging.getLogger(__name__)


def find_project_root() -> Path:
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
                    logger.info(
                        "Migrating legacy data directory from %s to %s",
                        legacy_dir,
                        new_dir,
                    )
                    new_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(legacy_dir), str(new_dir))
        except Exception as e:
            logger.error("Failed to migrate legacy directory: %s", e)
            return legacy_dir

    return new_dir


DATA_DIR = _get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _resolve_config_path() -> Path:
    env_path = os.getenv("AJA_CONFIG_PATH")
    if env_path and Path(env_path).exists():
        return Path(env_path).resolve()

    local_path = PROJECT_ROOT / "aja.json"
    data_path = DATA_DIR / "aja.json"

    # Emit warning if both exist and disagree on active/planner model
    if local_path.exists() and data_path.exists() and local_path.resolve() != data_path.resolve():
        try:
            with open(local_path, "r", encoding="utf-8") as f1, open(data_path, "r", encoding="utf-8") as f2:
                cfg_local = json.load(f1).get("swarm_settings", {})
                cfg_data = json.load(f2).get("swarm_settings", {})
                m_local = cfg_local.get("active_model") or cfg_local.get("models", {}).get("planner")
                m_data = cfg_data.get("active_model") or cfg_data.get("models", {}).get("planner")
                if m_local and m_data and m_local != m_data:
                    logger.warning(
                        "[Config Drift] Workspace '%s' (%s) and Global '%s' (%s) disagree; using workspace config.",
                        local_path, m_local, data_path, m_data,
                    )
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.debug("[Config] Drift check skipped: %s", e)

    if local_path.exists() and os.access(str(local_path.parent), os.W_OK):
        return local_path
    if data_path.exists():
        return data_path
    return local_path if local_path.exists() else data_path


CONFIG_PATH = _resolve_config_path()


# Load and validate configuration with Pydantic
def load_and_validate_config(config_path: Path | None = None) -> AJAConfig:
    target_path = config_path or CONFIG_PATH
    if target_path.exists():
        try:
            with target_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return AJAConfig.model_validate(data)
        except Exception as e:
            logger.error("Configuration validation failed for %s: %s", target_path, e)
            try:
                from rich import print as rprint

                rprint(
                    f"\n[bold red]Configuration Validation Error:[/] Malformed config in {target_path}"
                )
                rprint(f"[bold red]{e}[/]\n")
            except ImportError:
                print(
                    f"\nConfiguration Validation Error: Malformed config in {target_path}"
                )
                print(f"{e}\n")
            return AJAConfig()
    return AJAConfig()


# Loaded configuration object
CONFIG = load_and_validate_config()


# Model Selection
def _get_legacy_model(key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        models_cfg = getattr(getattr(CONFIG, "swarm_settings", None), "models", None)
        if models_cfg:
            v = getattr(models_cfg, key, None)
            if v:
                return str(v).strip()
    except (AttributeError, TypeError) as e:
        logger.debug("[Config] Legacy model lookup skipped: %s", e)
    return default


AJA_OPERATING_MODE = os.getenv(
    "AJA_OPERATING_MODE", getattr(getattr(CONFIG, "swarm_settings", None), "operating_mode", "hybrid") or "hybrid"
)
AJA_ACTIVE_MODEL = os.getenv(
    "AJA_ACTIVE_MODEL",
    os.getenv("AJA_MODEL")
    or getattr(getattr(CONFIG, "swarm_settings", None), "active_model", None)
    or _get_legacy_model("planner")
    or _get_legacy_model("worker")
    or "gate:qwen"
)
# Backward-compatibility shims: alias to AJA_ACTIVE_MODEL to prevent divergence
AJA_PLANNER_MODEL = os.getenv("AJA_PLANNER_MODEL", AJA_ACTIVE_MODEL)
AJA_WORKER_MODEL = os.getenv("AJA_WORKER_MODEL", AJA_ACTIVE_MODEL)
AJA_CRITIC_MODEL = os.getenv("AJA_CRITIC_MODEL", AJA_ACTIVE_MODEL)
AJA_VISION_MODEL = os.getenv(
    "AJA_VISION_MODEL",
    getattr(getattr(CONFIG, "swarm_settings", None), "vision_model", None)
    or _get_legacy_model("vision"),
)

# Telegram Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

# Model API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Local LLM (Llama Gold)
LLAMA_CPP_URL = os.getenv("LLAMA_CPP_URL", "http://localhost:8080/v1")
LLAMA_CPP_API_KEY = os.getenv("LLAMA_CPP_API_KEY", "local-secret")
