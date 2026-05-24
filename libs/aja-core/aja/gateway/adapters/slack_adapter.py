import logging
import asyncio
from typing import Dict, Any, Optional
from aja.gateway.base import BasePlatformAdapter, MessageEvent, MessageType

logger = logging.getLogger(__name__)

SLACK_AVAILABLE = False
try:
    from slack_sdk.web.async_client import AsyncWebClient
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    SLACK_AVAILABLE = True
except ImportError:
    pass

class SlackAdapter(BasePlatformAdapter):
    """
    AJA Slack Adapter (Assistant of Joint Agents).
    Supports socket mode connections to Slack workspaces.
    """

    def __init__(self, config: Dict[str, Any] or str):
        if isinstance(config, str):
            config = {"token": config}
        super().__init__(config)
        self.token = config.get("token")
        self.app_token = config.get("app_token")
        self.name = "slack"
        self._web_client = None
        self._socket_client = None
        self._queue = asyncio.Queue()
        self.gateway = None
        self.metrics = {
            "events_received": 0,
            "messages_sent": 0,
        }

    async def start(self, gateway):
        self.gateway = gateway
        if not self.token:
            logger.warning("[SlackAdapter] No Slack token provided. Skipping initialization.")
            return

        if not SLACK_AVAILABLE:
            logger.warning("[SlackAdapter] slack_sdk is not installed. Slack adapter running in simulated fallback.")
            self.is_running = True
            return

        self._web_client = AsyncWebClient(token=self.token)
        self.is_running = True

        if self.app_token:
            # Connect via SocketMode
            self._socket_client = SocketModeClient(
                app_token=self.app_token,
                web_client=self._web_client
            )
            
            async def process_slack_events(client, req, resp):
                if req.type == "events_api":
                    event = req.payload.get("event", {})
                    if event.get("type") == "message" and not event.get("bot_id"):
                        evt = MessageEvent(
                            platform="slack",
                            chat_id=str(event.get("channel")),
                            user_id=str(event.get("user")),
                            message_type=MessageType.TEXT,
                            text=event.get("text"),
                            message_id=str(event.get("client_msg_id")),
                            raw_event=event,
                        )
                        self.metrics["events_received"] += 1
                        await self._queue.put(evt)

            self._socket_client.socket_mode_request_listeners.append(process_slack_events)
            asyncio.create_task(self._socket_client.connect())
            logger.info("[SlackAdapter] Connected via SocketMode successfully.")

    async def stop(self):
        if self._socket_client and SLACK_AVAILABLE:
            await self._socket_client.close()
        self.is_running = False
        logger.info("[SlackAdapter] Stopped.")

    async def send_message(self, chat_id: str, text: str, **kwargs) -> Any:
        self.metrics["messages_sent"] += 1
        if SLACK_AVAILABLE and self._web_client:
            try:
                return await self._web_client.chat_postMessage(channel=chat_id, text=text)
            except Exception as e:
                logger.error(f"[SlackAdapter] Failed to send slack message: {e}")
        
        logger.info(f"[Slack Simulated Send] Channel {chat_id}: {text}")
        return {"status": "simulated", "chat_id": chat_id, "text": text}

    async def send_notification(self, chat_id: str, text: str, importance: str = "normal"):
        if importance == "high":
            await self.send_message(chat_id, f"🚨 *URGENT*: {text}")
        else:
            await self.send_message(chat_id, text)

    async def poll(self):
        while True:
            yield await self._queue.get()
