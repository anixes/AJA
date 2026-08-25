import asyncio
import logging
import os
from typing import List, Dict, Any, Optional
from aja.gateway.base import BasePlatformAdapter, MessageEvent
from aja.gateway.tg_client import TelegramAdapter
from aja.gateway.adapters.discord_adapter import DiscordAdapter
from aja.gateway.adapters.slack_adapter import SlackAdapter
from aja.gateway.orchestrator import _current_responder

logger = logging.getLogger(__name__)

class GatewayRunner:
    """
    Unified Session Broker managing concurrent platform adapters (Telegram, Discord, Slack).
    Maintains active user sessions across multiple gateway channels continuously.
    """

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.adapters: List[BasePlatformAdapter] = []
        self._tasks: List[asyncio.Task] = []
        self.is_running = False
        # No event lock needed: per-event routing uses an explicit ContextVar
        # responder (see process_event), so concurrent adapter pollers never
        # mutate shared orchestrator state.

    async def start(self):
        self.is_running = True
        
        # 1. Initialize Telegram
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
        if tg_token:
            tg_adapter = TelegramAdapter(tg_token)
            self.adapters.append(tg_adapter)
            self.orchestrator.telegram_adapter = tg_adapter
            logger.info("[GatewayRunner] Telegram adapter initialized.")

        # 2. Initialize Discord
        discord_token = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
        if discord_token:
            discord_adapter = DiscordAdapter(discord_token)
            self.adapters.append(discord_adapter)
            logger.info("[GatewayRunner] Discord adapter initialized.")

        # 3. Initialize Slack
        slack_token = os.getenv("SLACK_BOT_TOKEN") or os.getenv("SLACK_TOKEN")
        slack_app_token = os.getenv("SLACK_APP_TOKEN")
        if slack_token:
            slack_adapter = SlackAdapter({"token": slack_token, "app_token": slack_app_token})
            self.adapters.append(slack_adapter)
            logger.info("[GatewayRunner] Slack adapter initialized.")

        if not self.adapters:
            logger.warning("[GatewayRunner] No gateway tokens found in environment. Gateway running in CLI-only mode.")
            return

        # Start all adapters concurrently
        for adapter in self.adapters:
            try:
                await adapter.start(self.orchestrator)
                # Create a polling worker task for each adapter
                self._tasks.append(asyncio.create_task(self._poll_adapter(adapter)))
            except Exception as e:
                logger.error(f"[GatewayRunner] Failed to start {adapter.name}: {e}")

    async def _poll_adapter(self, adapter: BasePlatformAdapter):
        logger.info(f"[GatewayRunner] Polling event loop started for adapter '{adapter.name}'")
        try:
            async for event in adapter.poll():
                try:
                    await self.process_event(adapter, event)
                except Exception as e:
                    logger.error(f"[GatewayRunner] Exception processing event from {adapter.name}: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[GatewayRunner] Poll loop crashed for {adapter.name}: {e}")

    async def process_event(self, adapter: BasePlatformAdapter, event: MessageEvent):
        """
        Processes a platform-agnostic MessageEvent.
        Provides continuous session recovery by storing user interaction states.
        """
        # Explicit-responder routing: set the ContextVar the orchestrator's
        # _responder() reads, so replies/telemetry for THIS event go back to
        # the platform it came from. ContextVars are task-local, so concurrent
        # pollers can't clobber each other's routing — no lock or adapter swap.
        token = _current_responder.set(adapter)
        try:
            await self.orchestrator.handle_gateway_event(event)
        finally:
            _current_responder.reset(token)

    async def stop(self):
        self.is_running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        for adapter in self.adapters:
            try:
                await adapter.stop()
            except Exception as e:
                logger.error(f"[GatewayRunner] Failed to stop {adapter.name}: {e}")
        self.adapters = []
        logger.info("[GatewayRunner] All adapters stopped.")
