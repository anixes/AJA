import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aja.gateway.auth import is_user_authorized
from aja.gateway.base import BasePlatformAdapter, MessageEvent, MessageType

logger = logging.getLogger(__name__)

DISCORD_AVAILABLE = False
try:
    import discord
    from discord.ext import commands
    DISCORD_AVAILABLE = True
except ImportError:
    pass

from aja.runtime.event_bus import bus, EVENTS


class DiscordAdapter(BasePlatformAdapter):
    """
    AJA Discord Adapter (Assistant of Joint Agents).

    Functional parity with TelegramAdapter: bounded telemetry pipeline with a
    single dispatcher fanning out to per-channel queues, LanceDB journal
    polling with seen-ID dedup, interactive approval buttons backed by the
    shared approvals engine, attachment->vision input, resilient connect with
    exponential backoff, and full metrics/health reporting.
    """

    APPROVE_PREFIX = "aja:approve:"
    REJECT_PREFIX = "aja:reject:"
    _TELEMETRY_QUEUE_MAXSIZE = 1000
    _CHAT_QUEUE_MAXSIZE = 500

    def __init__(self, config: Dict[str, Any] or str):
        if isinstance(config, str):
            config = {"token": config}
        super().__init__(config)
        self.token = config.get("token")
        self.name = "discord"
        self.gateway = None
        self._bot = None
        self._bot_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._tail_tasks: Dict[str, asyncio.Task] = {}
        self._bus_handlers: list = []
        # Inbound user commands stay unbounded: silently dropping a command is
        # worse than backpressure. Telemetry is bounded.
        self._queue = asyncio.Queue()
        self.telemetry_queue: asyncio.Queue = asyncio.Queue(
            maxsize=self._TELEMETRY_QUEUE_MAXSIZE
        )
        # Per-channel fan-out queues fed by the single telemetry dispatcher.
        self._chat_queues: Dict[str, asyncio.Queue] = {}
        self.metrics: Dict[str, Any] = {
            "events_received": 0,
            "events_rejected": 0,
            "events_dequeued": 0,
            "messages_sent": 0,
            "send_failures": 0,
            "poll_retries": 0,
            "callback_handled": 0,
            "last_error": None,
            "last_error_at": None,
            "queue_lag_seconds": 0.0,
            "queue_size": 0,
        }

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self, gateway):
        self.gateway = gateway
        if not self.token:
            logger.warning(
                "[DiscordAdapter] No Discord token provided. Skipping initialization."
            )
            return

        if not DISCORD_AVAILABLE:
            logger.warning(
                "[DiscordAdapter] discord.py is not installed. "
                "Discord adapter running in simulated fallback."
            )
            self.is_running = True
            return

        intents = discord.Intents.default()
        intents.message_content = True
        self._bot = commands.Bot(command_prefix="/", intents=intents)

        @self._bot.event
        async def on_ready():
            logger.info(
                "[DiscordAdapter] Logged in as %s (%s)",
                self._bot.user,
                self._bot.user.id,
            )

        @self._bot.event
        async def on_message(message):
            await self._on_discord_message(message)

        @self._bot.event
        async def on_interaction(interaction):
            await self._on_discord_interaction(interaction)

        self.is_running = True
        # Subscribe to the event bus so bus events reach Discord telemetry
        # queues (parity with TelegramAdapter). Without this the standalone
        # gateway (aja.gateway.server) delivered zero Discord telemetry —
        # the bus->LanceDB bridge only loaded via the autonomous loop import.
        self._subscribe_bus_events()
        # Start background pipelines (LanceDB polling + telemetry dispatch);
        # they do not depend on the socket being live yet.
        self._poll_task = asyncio.create_task(self._poll_lancedb_events())
        self._dispatcher_task = asyncio.create_task(self._dispatch_telemetry())
        # Resilient connect with exponential backoff.
        self._bot_task = asyncio.create_task(self._run_bot())
        logger.info("[DiscordAdapter] Real Discord Client task started.")

    async def _run_bot(self):
        """Resilient connect: up to 5 attempts with exponential backoff."""
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                await self._bot.start(self.token)
                # Clean disconnect (stop() closed us).
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                wait = min(2**attempt, 30)
                self.metrics["poll_retries"] += 1
                self.metrics["last_error"] = str(e)
                self.metrics["last_error_at"] = datetime.now(timezone.utc).isoformat()
                logger.error(
                    "[DiscordAdapter] connect attempt %d failed: %s. Retrying in %ss...",
                    attempt + 1,
                    e,
                    wait,
                )
                await asyncio.sleep(wait)

    async def stop(self):
        self.is_running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None
        await self.stop_tails()
        # Unsubscribe bus handlers registered in start() so stopped adapters
        # stop accumulating events.
        for event_name, handler in self._bus_handlers:
            try:
                bus.unsubscribe(event_name, handler)
            except Exception as e:
                logger.debug(
                    "Failed to unsubscribe handler for %s: %s", event_name, e
                )
        self._bus_handlers.clear()
        if self._bot_task:
            self._bot_task.cancel()
            try:
                await self._bot_task
            except asyncio.CancelledError:
                pass
            self._bot_task = None
        if self._bot and DISCORD_AVAILABLE:
            try:
                await self._bot.close()
            except Exception as e:
                logger.debug("[DiscordAdapter] Error closing bot: %s", e)
        self._bot = None
        logger.info("[DiscordAdapter] Stopped.")

    # ------------------------------------------------------------------ #
    # Inbound messages (incl. vision attachments)
    # ------------------------------------------------------------------ #

    async def _on_discord_message(self, message):
        if getattr(message, "author", None) == (self._bot.user if self._bot else None):
            return

        user_id = str(getattr(getattr(message, "author", None), "id", ""))
        channel_id = str(getattr(getattr(message, "channel", None), "id", ""))
        if not is_user_authorized("discord", user_id):
            logger.warning(
                "[DiscordAdapter] Unauthorized event dropped (user_id=%s, channel=%s)",
                user_id,
                channel_id,
            )
            self.metrics["events_rejected"] += 1
            try:
                await message.reply(
                    "🚫 Access Denied. Your Discord user is not authorized to command AJA.\n"
                    f"Add your ID to the `.env` file: `DISCORD_ALLOWED_USER_IDS={user_id}`"
                )
            except Exception as e:
                logger.debug("[DiscordAdapter] Could not deliver denial notice: %s", e)
            return

        text_content = getattr(message, "content", None) or ""
        media_urls: List[str] = []
        msg_type = MessageType.TEXT

        attachments = getattr(message, "attachments", None) or []
        for attachment in attachments:
            content_type = getattr(attachment, "content_type", "") or ""
            if not content_type.startswith("image/"):
                continue
            try:
                data = await attachment.read()
                b64_data = base64.b64encode(data).decode("utf-8")
                media_urls.append(f"data:{content_type};base64,{b64_data}")
            except Exception as e:
                logger.error("[DiscordAdapter] Failed to download attachment: %s", e)
        if media_urls:
            msg_type = MessageType.PHOTO
            if not text_content:
                text_content = (
                    "What can you see in this image? Please analyze and describe it in detail."
                )

        if not text_content and not media_urls:
            return

        event = MessageEvent(
            platform="discord",
            chat_id=channel_id,
            user_id=user_id,
            message_type=msg_type,
            text=text_content,
            media_urls=media_urls,
            message_id=str(getattr(message, "id", "")),
            raw_event=message,
        )
        logger.info(
            "Received message (%s) from %s: %s",
            msg_type.value,
            event.user_id,
            (event.text or "")[:50],
        )
        self.metrics["events_received"] += 1
        self.metrics["queue_size"] = self._queue.qsize() + 1
        self.metrics["queue_lag_seconds"] = 0.0
        await self._queue.put(event)
        return event

    async def poll(self):
        """Async generator for orchestrator to consume events."""
        while True:
            event = await self._queue.get()
            self.metrics["events_dequeued"] += 1
            self.metrics["queue_size"] = self._queue.qsize()
            self.metrics["queue_lag_seconds"] = self._compute_queue_lag_seconds(
                event.timestamp
            )
            yield event

    # ------------------------------------------------------------------ #
    # Approval interactions (shared engine delegation)
    # ------------------------------------------------------------------ #

    def _parse_approval_custom_id(self, custom_id: str):
        """Returns (action, mission_id) or None for non-approval widgets."""
        if custom_id.startswith(self.APPROVE_PREFIX):
            return "approve", custom_id[len(self.APPROVE_PREFIX):]
        if custom_id.startswith(self.REJECT_PREFIX):
            return "reject", custom_id[len(self.REJECT_PREFIX):]
        return None

    async def _handle_approval_interaction(self, interaction, action: str, mission_id: str) -> str:
        """Auth check (adapter-owned allowlist) then shared resolution."""
        callback_user_id = (
            str(interaction.user.id) if getattr(interaction, "user", None) else ""
        )
        self.metrics["callback_handled"] += 1
        if not is_user_authorized("discord", callback_user_id):
            logger.critical(
                "Security Alert: Unauthorized Discord callback attempt by user %s",
                callback_user_id,
            )
            return "🚫 Unauthorized callback action."

        from aja.gateway.approvals import resolve_approval

        _, message = await resolve_approval(
            platform="discord",
            user_id=callback_user_id,
            mission_id=mission_id,
            action=action,
        )
        return message

    async def _on_discord_interaction(self, interaction):
        custom_id = getattr(interaction, "data", {}).get("custom_id", "") if hasattr(
            interaction, "data"
        ) else getattr(interaction, "custom_id", "")
        parsed = self._parse_approval_custom_id(custom_id or "")
        if not parsed:
            return
        action, mission_id = parsed
        outcome = await self._handle_approval_interaction(interaction, action, mission_id)
        response = getattr(interaction, "response", None)
        try:
            await response.edit_message(content=outcome, view=None)
        except Exception as e:
            logger.debug("[DiscordAdapter] Could not edit approval message: %s", e)

    def _build_approval_view(self, mission_id: str):
        """Builds the interactive approval View (requires discord.py)."""
        if not DISCORD_AVAILABLE:
            return None

        adapter = self

        class ApprovalView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                for label, style, prefix, action in (
                    ("✅ Approve", discord.ButtonStyle.success, adapter.APPROVE_PREFIX, "approve"),
                    ("❌ Reject", discord.ButtonStyle.danger, adapter.REJECT_PREFIX, "reject"),
                ):
                    button = discord.ui.Button(
                        label=label,
                        style=style,
                        custom_id=f"{prefix}{mission_id}",
                    )
                    button.callback = self._make_callback(action)
                    self.add_item(button)

            def _make_callback(self, action: str):
                async def _cb(interaction):
                    outcome = await adapter._handle_approval_interaction(
                        interaction, action, mission_id
                    )
                    try:
                        await interaction.response.edit_message(content=outcome, view=None)
                    except Exception as e:
                        logger.debug(
                            "[DiscordAdapter] Could not edit approval message: %s", e
                        )

                return _cb

        return ApprovalView()

    # ------------------------------------------------------------------ #
    # Outbound messaging
    # ------------------------------------------------------------------ #

    async def send_message(self, chat_id: str, text: str, view=None, **kwargs) -> Any:
        if text is None:
            text = ""
        try:
            if DISCORD_AVAILABLE and self._bot:
                channel = self._bot.get_channel(int(chat_id))
                if not channel:
                    channel = await self._bot.fetch_channel(int(chat_id))
                if channel:
                    result = await channel.send(text, view=view, **kwargs)
                    self.metrics["messages_sent"] += 1
                    return result
        except Exception as e:
            logger.error(
                "[DiscordAdapter] Failed to send message to channel %s: %s", chat_id, e
            )
            self.metrics["send_failures"] += 1
            self.metrics["last_error"] = str(e)
            self.metrics["last_error_at"] = datetime.now(timezone.utc).isoformat()
            return None
        # Fallback simulated print
        logger.info("[Discord Simulated Send] Channel %s: %s", chat_id, text)
        return {"status": "simulated", "chat_id": chat_id, "text": text}

    async def send_notification(
        self, chat_id: str, text: str, importance: str = "normal"
    ):
        if importance == "high":
            await self.send_message(chat_id, f"⚠️ **URGENT**: {text}")
        elif importance == "low":
            # Progress updates stay silent on Discord (no notification ping).
            await self.send_message(chat_id, text)
        else:
            await self.send_message(chat_id, text)

    # ------------------------------------------------------------------ #
    # Telemetry pipeline (parity with TelegramAdapter)
    # ------------------------------------------------------------------ #

    def _subscribe_bus_events(self):
        for event_name in EVENTS.values():
            handler = self._make_event_handler(event_name)
            bus.subscribe(event_name, handler)
            self._bus_handlers.append((event_name, handler))

    def _make_event_handler(self, event_name: str):
        def handler(payload: dict):
            try:
                event_id = uuid.uuid4().hex[:8]
                target = payload.get("node_id", payload.get("mission_id", "system"))
                status = "INFO"
                if "FAILED" in event_name:
                    status = "ERROR"
                elif "SUCCESS" in event_name:
                    status = "SUCCESS"

                message = payload.get("message", str(payload))
                if event_name == EVENTS.get("PLAN_CREATED", "PLAN_CREATED"):
                    message = payload.get("plan_summary", "Plan created.")

                ev = {
                    "event_id": event_id,
                    "kind": event_name,
                    "target": target,
                    "status": status,
                    "message": message,
                    "command": payload.get("command", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._put_telemetry(ev)
            except Exception as e:
                logger.error(f"[DiscordAdapter] Failed to queue event {event_name}: {e}")

        return handler

    def _put_telemetry(self, ev: dict):
        """Bounded enqueue with drop-oldest so a stalled consumer cannot grow
        memory without limit. Approval requests are never dropped."""
        q = self.telemetry_queue
        if ev.get("kind") == "AWAITING_APPROVAL":
            if q.full():
                try:
                    dropped = q.get_nowait()
                    logger.warning(
                        "Telemetry queue full; dropped oldest event %s",
                        dropped.get("event_id"),
                    )
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
                    "Telemetry queue full; dropped event %s (%s) for %s",
                    dropped.get("event_id"),
                    dropped.get("kind"),
                    ev.get("kind"),
                )
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def start_tail(self, chat_id: str) -> None:
        """Registers a per-channel telemetry tail task with tracked lifecycle."""
        chat_key = str(chat_id)
        if chat_key in self._tail_tasks and not self._tail_tasks[chat_key].done():
            return
        if self.is_running and (
            self._dispatcher_task is None or self._dispatcher_task.done()
        ):
            self._dispatcher_task = asyncio.create_task(self._dispatch_telemetry())
        self._chat_queues.setdefault(
            chat_key, asyncio.Queue(maxsize=self._CHAT_QUEUE_MAXSIZE)
        )
        self._tail_tasks[chat_key] = asyncio.create_task(self.tail_events(chat_key))

    async def stop_tails(self):
        for task in list(self._tail_tasks.values()):
            task.cancel()
        self._tail_tasks.clear()
        self._chat_queues.clear()

    async def _dispatch_telemetry(self):
        """Single consumer of the shared telemetry queue that fans each event
        out to every subscribed channel's queue."""
        while True:
            try:
                ev = await self.telemetry_queue.get()
                for chat_q in list(self._chat_queues.values()):
                    try:
                        chat_q.put_nowait(ev)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Chat telemetry queue full; dropping event %s",
                            ev.get("event_id"),
                        )
                self.telemetry_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry dispatcher error: {e}")
                await asyncio.sleep(1)

    async def tail_events(self, chat_id: str):
        """
        Background task to tail this channel's telemetry queue and forward to
        Discord. Events are fanned out by _dispatch_telemetry().
        """
        logger.info(f"Starting event-driven telemetry bridge for channel_id: {chat_id}")
        chat_queue = self._chat_queues.setdefault(
            str(chat_id), asyncio.Queue(maxsize=self._CHAT_QUEUE_MAXSIZE)
        )
        import time

        while self.is_running:
            try:
                ev = await chat_queue.get()

                # Dynamic History Update: Inject significant events into
                # conversational session history
                if hasattr(self, "gateway") and self.gateway:
                    try:
                        session = await asyncio.to_thread(
                            self.gateway.gateway_state.get_session, chat_id
                        )
                        if ev["kind"] in [
                            "PLAN_CREATED",
                            "MISSION_RESULT",
                            "MISSION_DONE",
                            "MISSION_FAILED",
                            "NODE_FAILED",
                            "NODE_REJECTED",
                            "NODE_APPROVED",
                        ]:
                            system_note = f"[System Note: {ev['message']}]"
                            session["history"].append(
                                {
                                    "role": "system",
                                    "text": system_note,
                                    "time": time.time(),
                                }
                            )
                            await asyncio.to_thread(
                                self.gateway.gateway_state.update_session,
                                chat_id,
                                session,
                            )
                    except Exception as he:
                        logger.error(
                            f"Failed to update session history from event: {he}"
                        )

                if ev["kind"] == "AWAITING_APPROVAL":
                    view = self._build_approval_view(ev["target"])
                    sent = await self.send_message(
                        chat_id,
                        f"⚠️ APPROVAL REQUIRED: {ev['message']}",
                        view=view,
                    )
                    if view is not None and sent is not None:
                        try:
                            self._bot.add_view(view)
                        except Exception:
                            pass
                else:
                    importance = "low"
                    if ev["status"] == "ERROR":
                        importance = "high"
                    elif ev["kind"] in [
                        "MISSION_CREATED",
                        "MISSION_DONE",
                        "PLAN_CREATED",
                        "MISSION_RESULT",
                    ]:
                        importance = "normal"

                    msg = f"[{ev['status']}] {ev['message']}"
                    await self.send_notification(chat_id, msg, importance=importance)
                chat_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry tail error: {e}")
                self.metrics["last_error"] = str(e)
                self.metrics["last_error_at"] = datetime.now(timezone.utc).isoformat()
                await asyncio.sleep(1)

    # ------------------------------------------------------------------ #
    # LanceDB journal poller (parity with TelegramAdapter)
    # ------------------------------------------------------------------ #

    async def _poll_lancedb_events(self):
        """
        Polls the LanceDB aja_runtime_events table for new events in a
        non-blocking background loop. Deduplicates via a local set of recently
        processed event IDs.
        """
        logger.info("Starting background LanceDB event polling task.")
        seen_event_ids = set()

        # Pre-populate seen event IDs so we don't dump historical events.
        try:
            from aja.memory.secretary import get_aja_memory

            memory = get_aja_memory()
            table = memory.db.open_table("aja_runtime_events")
            existing_events = await asyncio.to_thread(table.search().limit(200).to_list)
            for ev in existing_events:
                eid = ev.get("event_id")
                if eid:
                    seen_event_ids.add(eid)
            logger.info(f"Pre-populated {len(seen_event_ids)} existing event IDs.")
        except Exception as e:
            logger.error(f"Failed to pre-populate seen event IDs: {e}")

        while self.is_running:
            try:
                from aja.memory.secretary import get_aja_memory

                memory = get_aja_memory()
                table = memory.db.open_table("aja_runtime_events")

                # Fetch recent events off the event loop (blocking disk I/O).
                events = await asyncio.to_thread(table.search().limit(100).to_list)

                for ev in events:
                    eid = ev.get("event_id")
                    if not eid or eid in seen_event_ids:
                        continue

                    # eid is marked seen only AFTER successful enqueue below,
                    # so a crash mid-processing retries next tick instead of
                    # permanently dropping the row.

                    try:
                        kind = ev.get("kind")
                        target = ev.get("target")
                        status = (ev.get("status") or "success").upper()
                        if status == "FAILED":
                            status = "ERROR"
                        elif status == "SUCCESS":
                            status = "SUCCESS"

                        metadata_raw = ev.get("metadata_json") or "{}"
                        try:
                            metadata = json.loads(metadata_raw)
                        except Exception:
                            metadata = {}

                        message = (
                            metadata.get("message")
                            or metadata.get("plan_summary")
                            or ev.get("message")
                        )
                        if not message:
                            message = f"Event: {kind} on target {target}"

                        payload = {
                            "event_id": eid,
                            "kind": kind,
                            "target": target,
                            "status": status,
                            "message": message,
                            "command": ev.get("command", ""),
                            "timestamp": ev.get("timestamp")
                            or datetime.now(timezone.utc).isoformat(),
                        }
                        await self.telemetry_queue.put(payload)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(f"Failed to process event {eid}: {e}")
                        continue

                    seen_event_ids.add(eid)
                    if len(seen_event_ids) > 5000:
                        seen_event_ids = set(list(seen_event_ids)[-4000:])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error polling LanceDB events: {e}")

            await asyncio.sleep(1)

    # ------------------------------------------------------------------ #
    # Observability
    # ------------------------------------------------------------------ #

    def get_health_snapshot(self) -> Dict[str, Any]:
        return {
            "adapter": self.name,
            "is_running": self.is_running,
            **self.metrics,
        }

    def _compute_queue_lag_seconds(self, event_timestamp: Any) -> float:
        now_ts = datetime.now(timezone.utc).timestamp()
        try:
            if isinstance(event_timestamp, (int, float)):
                return max(0.0, now_ts - float(event_timestamp))
            if isinstance(event_timestamp, str):
                parsed = datetime.fromisoformat(event_timestamp.replace("Z", "+00:00"))
                return max(0.0, now_ts - parsed.timestamp())
        except Exception:
            pass
        return 0.0
