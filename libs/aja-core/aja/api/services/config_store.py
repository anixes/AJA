"""
Config Store — JSON persistence for the bridge /config routes.

Extracted from api/bridge.py so the persistence logic is testable and
reusable independently of the FastAPI app. Pure functions only; the path is
passed explicitly with an optional default.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {"provider": "openrouter", "api_key": "", "model": ""}


def load_config(config_path: Path | None = None) -> dict:
    path = _resolve_path(config_path)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to parse %s; returning defaults: %s", path, e)
    return dict(DEFAULT_CONFIG)


def save_config(data: dict, config_path: Path | None = None) -> None:
    path = _resolve_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def mask_api_key(key: str) -> str:
    """Mask the API key for safety — only show last 4 chars."""
    if len(key) > 4:
        return ("*" * max(0, len(key) - 4)) + key[-4:]
    return key


def _resolve_path(config_path: Path | None) -> Path:
    if config_path is not None:
        return config_path
    from aja.api.app_context import get_app_context

    return get_app_context().config_path
