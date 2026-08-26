"""
AppContext — shared application state for the API bridge.

Extracted from api/bridge.py so services can receive context explicitly
instead of importing bridge module globals. from_env() reads environment
variables ONCE to preserve bridge's import-time semantics (some tests
monkeypatch env before importing aja.api.bridge).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from aja.config import DATA_DIR


@dataclass
class AppContext:
    """Immutable-by-convention snapshot of bridge configuration + shared state."""

    # Auth
    api_token: str
    default_api_token: str

    # Telegram
    telegram_bot_token: str
    telegram_allowed_user_id: str
    telegram_webhook_secret: str
    telegram_command_timeout: int

    # Paths
    data_dir: Path
    history_path: Path
    pending_path: Path
    audit_path: Path
    runtime_state_path: Path
    baton_dir: Path
    config_path: Path

    # Shared state / collaborators
    memory_provider: Callable[[], Any]
    ws_manager: Any = None
    approval_claim_locks: dict = field(default_factory=dict)
    trigger_counters: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "AppContext":
        """Read configuration from the environment exactly once."""
        default_api_token = "dev-token-123"
        return cls(
            api_token=os.getenv("AJA_API_TOKEN", default_api_token),
            default_api_token=default_api_token,
            telegram_bot_token=os.getenv("TELEGRAM_TOKEN")
            or os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_user_id=os.getenv("TELEGRAM_ALLOWED_USER_ID", ""),
            telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", ""),
            telegram_command_timeout=int(os.getenv("TELEGRAM_COMMAND_TIMEOUT", "60")),
            data_dir=DATA_DIR,
            history_path=DATA_DIR / "telegram-history.jsonl",
            pending_path=DATA_DIR / "telegram-pending.json",
            audit_path=DATA_DIR / "approval-audit.jsonl",
            runtime_state_path=DATA_DIR / "runtime-state.json",
            baton_dir=DATA_DIR / "batons",
            config_path=DATA_DIR / "config.json",
            memory_provider=lambda: None,
        )


_app_context: AppContext | None = None


def get_app_context() -> AppContext:
    """Lazy module-level singleton; built once on first access."""
    global _app_context
    if _app_context is None:
        _app_context = AppContext.from_env()
    return _app_context


def set_app_context(ctx: AppContext | None) -> None:
    """Replace the singleton (used by tests and future wiring)."""
    global _app_context
    _app_context = ctx
