"""Telegram command-menu registration (setMyCommands).

Registers the core slash-command list so Telegram clients show the native
command menu. Registration is best-effort: it must never block or fail
gateway startup.
"""
import logging
from typing import Any, List

logger = logging.getLogger(__name__)

# (command, description) — descriptions must be 1-256 chars, no newlines.
CORE_COMMANDS: List[tuple] = [
    ("start", "Introduce AJA and check the connection"),
    ("help", "Show what I can do"),
    ("status", "Mission & system status report"),
    ("kanban", "Show the mission kanban board"),
    ("missions", "List missions"),
    ("models", "List / switch available models"),
    ("doctor", "Run a system health check"),
    ("clear", "Clear this chat's session history"),
]


async def register_command_menu(bot: Any) -> bool:
    """Calls setMyCommands with the core command list.

    Returns True on success; logs (never raises) on failure so callers can
    fire-and-forget it at startup.
    """
    if bot is None:
        logger.debug("register_command_menu: no bot instance; skipping.")
        return False
    try:
        await bot.set_my_commands(
            [
                {"command": cmd, "description": desc}
                for cmd, desc in CORE_COMMANDS
            ]
        )
        logger.info("Telegram command menu registered (%d commands).", len(CORE_COMMANDS))
        return True
    except Exception as e:
        # Menu registration must never block/fail startup.
        logger.error("Failed to register Telegram command menu: %s", e)
        return False


def spawn_menu_registration(adapter: Any) -> Any:
    """Fire-and-forget task wrapper for use in adapter.start()."""
    try:
        return asyncio_create_task(register_command_menu(getattr(adapter, "_bot", None)))
    except Exception as e:
        logger.debug("Could not schedule menu registration: %s", e)
        return None


def asyncio_create_task(coro):
    import asyncio

    return asyncio.create_task(coro)
