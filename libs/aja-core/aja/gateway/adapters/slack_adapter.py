import logging
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from aja.gateway.auth import is_user_authorized
from aja.gateway.base import BasePlatformAdapter, MessageEvent, MessageType
from aja.runtime.event_bus import bus, EVENTS

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
        # Per-channel telemetry tail state (adapter contract parity with
        # Telegram/Discord adapters; see start_tail/tail_events/stop_tails).
        self._tail_tasks: Dict[str, asyncio.Task] = {}
        self._chat_queues: Dict[str, asyncio.Queue] = {}
        self._bus_handlers: list = []
        self.telemetry_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._dispatcher_task: Optional[asyncio.Task] = None
        self.metrics = {
            "events_received": 0,
            "events_rejected": 0,
            "messages_sent": 0,
        }

    async def start(self, gateway):
        self.gateway = gateway
        # Subscribe to the event bus in BOTH paths (real + simulated): Slack
        # missions previously promised live reports that never arrived —
        # nothing fed the per-channel queues.
        self._subscribe_bus_events()
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
                        slack_user_id = str(event.get("user"))
                        if not is_user_authorized("slack", slack_user_id):
                            logger.warning(
                                "[SlackAdapter] Unauthorized event dropped (user_id=%s, channel=%s): '%s'",
                                slack_user_id,
                                event.get("channel"),
                                event.get("text"),
                            )
                            self.metrics["events_rejected"] += 1
                            try:
                                await self._web_client.chat_postMessage(
                                    channel=str(event.get("channel")),
                                    text=(
                                        "🚫 Access Denied. Your Slack user is not authorized to command AJA.\n"
                                        f"Add your ID to the `.env` file: `SLACK_ALLOWED_USER_IDS={slack_user_id}`"
                                    ),
                                )
                            except Exception as e:
                                logger.debug(f"[SlackAdapter] Could not deliver denial notice: {e}")
                            return
                        text_content = event.get("text") or ""
                        if not text_content:
                            # Attachment-only / file_share events can carry a
                            # NULL text. Drop them here rather than letting the
                            # orchestrator coerce empty text into its vision
                            # default prompt (no media is attached on Slack).
                            logger.debug(
                                "[SlackAdapter] Dropping message without text (channel=%s)",
                                event.get("channel"),
                            )
                            return
                        evt = MessageEvent(
                            platform="slack",
                            chat_id=str(event.get("channel")),
                            user_id=str(event.get("user")),
                            message_type=MessageType.TEXT,
                            text=text_content,
                            message_id=str(event.get("client_msg_id")),
                            raw_event=event,
                        )
                        self.metrics["events_received"] += 1
                        await self._queue.put(evt)

            self._socket_client.socket_mode_request_listeners.append(process_slack_events)
            asyncio.create_task(self._socket_client.connect())
            logger.info("[SlackAdapter] Connected via SocketMode successfully.")

    async def stop(self):
        await self.stop_tails()
        for event_name, handler in self._bus_handlers:
            try:
                bus.unsubscribe(event_name, handler)
            except Exception as e:
                logger.debug("Failed to unsubscribe handler for %s: %s", event_name, e)
        self._bus_handlers.clear()
        if self._dispatcher_task and not self._dispatcher_task.done():
            self._dispatcher_task.cancel()
        self._dispatcher_task = None
        if self._socket_client and SLACK_AVAILABLE:
            await self._socket_client.close()
        self.is_running = False

    # ------------------------------------------------------------------ #
    # Telemetry pipeline (bus -> shared queue -> per-channel fan-out),
    # mirroring TelegramAdapter so Slack missions deliver live reports.
    # ------------------------------------------------------------------ #

    def _subscribe_bus_events(self):
        for event_name in EVENTS.values():
            handler = self._make_event_handler(event_name)
            bus.subscribe(event_name, handler)
            self._bus_handlers.append((event_name, handler))

    def _make_event_handler(self, event_name: str):
        def handler(payload: dict):
            try:
                target = payload.get("node_id", payload.get("mission_id", "system"))
                status = "INFO"
                if "FAILED" in event_name:
                    status = "ERROR"
                elif "SUCCESS" in event_name:
                    status = "SUCCESS"

                message = payload.get("message", str(payload))
                if event_name == EVENTS.get("PLAN_CREATED", "PLAN_CREATED"):
                    message = payload.get("plan_summary", "Plan created.")

                self._put_telemetry(
                    {
                        "event_id": uuid.uuid4().hex[:8],
                        "kind": event_name,
                        "target": target,
                        "status": status,
                        "message": message,
                        "command": payload.get("command", ""),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as e:
                logger.error(f"[SlackAdapter] Failed to queue event {event_name}: {e}")

        return handler

    def _put_telemetry(self, ev: dict):
        """Bounded enqueue with drop-oldest; approvals are never dropped."""
        q = self.telemetry_queue
        if ev.get("kind") == "AWAITING_APPROVAL":
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(ev)
            return
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            try:
                dropped = q.get_nowait()
                q.put_nowait(ev)
                logger.warning(
                    "Telemetry queue full; dropped event %s (%s)",
                    dropped.get("event_id"),
                    dropped.get("kind"),
                )
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def _dispatch_telemetry(self):
        """Single consumer fanning each event out to every subscribed channel
        queue — without this, N competing consumers delivered each event to
        exactly ONE arbitrary channel."""
        while True:
            try:
                ev = await self.telemetry_queue.get()
                for chat_q in list(self._chat_queues.values()):
                    try:
                        chat_q.put_nowait(ev)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Channel telemetry queue full; dropping event %s",
                            ev.get("event_id"),
                        )
                self.telemetry_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry dispatcher error: {e}")
                await asyncio.sleep(1)
        logger.info("[SlackAdapter] Stopped.")

    # ------------------------------------------------------------------ #
    # Telemetry tail contract (parity with Telegram/Discord adapters)
    # ------------------------------------------------------------------ #

    def start_tail(self, chat_id: str) -> None:
        """Registers a per-channel telemetry tail task with tracked lifecycle."""
        chat_key = str(chat_id)
        if chat_key in self._tail_tasks and not self._tail_tasks[chat_key].done():
            return
        # Ensure the shared-queue dispatcher is running so events actually
        # reach this channel's queue.
        if self.is_running and (self._dispatcher_task is None or self._dispatcher_task.done()):
            self._dispatcher_task = asyncio.create_task(self._dispatch_telemetry())
        self._chat_queues.setdefault(chat_key, asyncio.Queue(maxsize=500))
        self._tail_tasks[chat_key] = asyncio.create_task(self.tail_events(chat_key))

    async def stop_tails(self):
        for task in list(self._tail_tasks.values()):
            task.cancel()
        self._tail_tasks.clear()
        self._chat_queues.clear()

    async def tail_events(self, chat_id: str):
        """Tails this channel's telemetry queue and forwards events to Slack."""
        logger.info(f"Starting telemetry bridge for channel_id: {chat_id}")
        chat_queue = self._chat_queues.setdefault(str(chat_id), asyncio.Queue(maxsize=500))
        while self.is_running:
            try:
                ev = await chat_queue.get()
                msg = f"[{ev.get('status') or 'INFO'}] {ev.get('message', '')}"
                await self.send_notification(chat_id, msg)
                chat_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry tail error: {e}")
                await asyncio.sleep(1)

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
