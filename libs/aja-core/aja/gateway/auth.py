"""Unified per-platform gateway authorization.

Security policy (fail-safe, not fail-open):
- If a platform allowlist is configured, the sender must match one entry
  (comma-separated lists supported; "*" = explicit allow-all).
- If NO allowlist is configured for that platform: DENY when that platform's
  bot token is configured (gateway is remotely reachable and unlisted remote
  users could trigger shell-executing missions), otherwise ALLOW (local-only
  development setup where nothing is reachable remotely).

Env contract:
- Allowlists : TELEGRAM_ALLOWED_USER_ID / DISCORD_ALLOWED_USER_IDS / SLACK_ALLOWED_USER_IDS
- Bot tokens : TELEGRAM_BOT_TOKEN|TELEGRAM_TOKEN / DISCORD_BOT_TOKEN|DISCORD_TOKEN / SLACK_BOT_TOKEN|SLACK_TOKEN

Fallback chain: os.getenv first, then aja.config module attribute.
"""

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger("aja.gateway.auth")

PLATFORM_ALLOWLIST_ENVS = {
    "telegram": ("TELEGRAM_ALLOWED_USER_ID",),
    "discord": ("DISCORD_ALLOWED_USER_IDS", "DISCORD_ALLOWED_USER_ID"),
    "slack": ("SLACK_ALLOWED_USER_IDS", "SLACK_ALLOWED_USER_ID"),
}

PLATFORM_TOKEN_ENVS = {
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"),
    "discord": ("DISCORD_BOT_TOKEN", "DISCORD_TOKEN"),
    "slack": ("SLACK_BOT_TOKEN", "SLACK_TOKEN"),
}

SUPPORTED_PLATFORMS = tuple(PLATFORM_ALLOWLIST_ENVS.keys())


def _env_or_config(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is not None:
        return value
    try:
        from aja import config as aja_config
        return getattr(aja_config, name, None)
    except Exception:
        return None


def get_allowlist(platform: str) -> Optional[str]:
    """Returns the raw configured allowlist string, or None when unset/blank."""
    env_names = PLATFORM_ALLOWLIST_ENVS.get(platform.lower())
    if not env_names:
        raise ValueError(f"Unsupported platform: {platform!r}")
    for env_name in env_names:
        raw = _env_or_config(env_name)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def has_bot_token(platform: str) -> bool:
    env_names = PLATFORM_TOKEN_ENVS.get(platform.lower())
    if not env_names:
        raise ValueError(f"Unsupported platform: {platform!r}")
    return any(_env_or_config(env_name) for env_name in env_names)


def get_platform_posture(platform: str) -> Tuple[bool, bool]:
    """Returns (token_configured, allowlist_set) for diagnostics reporting."""
    return (has_bot_token(platform), get_allowlist(platform) is not None)


def is_user_authorized(platform: str, user_id: object) -> bool:
    platform_key = (platform or "").lower()
    raw_allowlist = get_allowlist(platform_key)

    if raw_allowlist == "*":
        return True

    if raw_allowlist:
        allowed_ids = {entry.strip() for entry in raw_allowlist.split(",") if entry.strip()}
        authorized = str(user_id) in allowed_ids
        if not authorized:
            logger.warning(
                "%s_event_unauthorized: user_id %r not in allowlist",
                platform_key,
                str(user_id),
            )
        return authorized

    # No allowlist at all: only deny when the platform is remotely reachable.
    if has_bot_token(platform_key):
        logger.warning(
            "%s authorization is OPEN: bot token configured but no allowlist set "
            "(%s). Remote users will be DENIED until an allowlist is configured.",
            platform_key,
            PLATFORM_ALLOWLIST_ENVS[platform_key][0],
        )
        return False
    return True
