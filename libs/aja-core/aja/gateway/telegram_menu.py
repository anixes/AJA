"""Telegram command-menu registration (setMyCommands).

Registers the core slash-command list so Telegram clients show the native
command menu. Registration is best-effort: it must never block or fail
gateway startup.
"""
import logging
import os
from typing import Any, List

logger = logging.getLogger(__name__)

# (command, description) — descriptions must be 1-256 chars, no newlines.
CORE_COMMANDS: List[tuple] = [
    ("start", "Introduce AJA and check connection"),
    ("help", "Show capabilities and commands"),
    ("status", "Mission and system metrics"),
    ("kanban", "Interactive mission board"),
    ("missions", "List all missions"),
    ("models", "List / switch available models"),
    ("doctor", "Run system health check"),
    ("clear", "Clear chat session history"),
]


async def register_command_menu(bot: Any) -> bool:
    """Calls setMyCommands with the core command list and configures WebApp MenuButton.

    Returns True on success; logs (never raises) on failure so callers can
    fire-and-forget it at startup.
    """
    if bot is None:
        logger.debug("register_command_menu: no bot instance; skipping.")
        return False
    try:
        try:
            from telegram import BotCommand

            commands = [BotCommand(cmd, desc) for cmd, desc in CORE_COMMANDS]
        except Exception:
            commands = [(cmd, desc) for cmd, desc in CORE_COMMANDS]
        await bot.set_my_commands(commands)
        logger.info("Telegram command menu registered (%d commands).", len(CORE_COMMANDS))

        webapp_url = os.getenv("AJA_WEBAPP_URL") or os.getenv("WEBAPP_URL")
        if webapp_url:
            try:
                from telegram import MenuButtonWebApp, WebAppInfo

                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        text="🚀 Mission Control",
                        web_app=WebAppInfo(url=webapp_url),
                    )
                )
                logger.info("Telegram Chat Menu Button set to WebApp: %s", webapp_url)
            except Exception as e:
                logger.debug("MenuButtonWebApp setup skipped: %s", e)

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
