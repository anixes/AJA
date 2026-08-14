import os
import json
import asyncio
import time
import uuid
import logging
import subprocess
import sys
from typing import List, Dict, Any, Optional, Set
from aja import aja_native
from aja.config import PROJECT_ROOT, DATA_DIR, TELEGRAM_ALLOWED_USER_ID, AJA_PLANNER_MODEL
from aja.runtime.memory import MemoryTree
from aja.runtime.handover import BatonManager
from aja.memory.vector import VectorMemory
from aja.api.universal import UniversalRequest, UniversalItem, ContentBlock, Role
from aja.api.acp import ACPBridge, ACPClient
from aja.llm import completion
from aja.gateway.tg_client import TelegramAdapter
from aja.memory.secretary import AJAMemory
from aja.gateway.persistence import GatewayState
from aja.gateway.vision import VisionBridge
from aja.gateway.base import MessageEvent, MessageType

logger = logging.getLogger(__name__)


class UnifiedGateway:
    """
    The main integration hub for AJA.
    Combines high-performance orchestration core logic with the AJA Gateway.
    """

    def __init__(self, model_id: Optional[str] = None):
        self.model_id = model_id or AJA_PLANNER_MODEL
        self.memory = MemoryTree()

        # DUAL BRAIN: MemoryTree (Structured) + VectorMemory (LanceDB/Semantic)
        self.vector_memory = VectorMemory(table_name="mission_semantic")
        from aja.memory.secretary import get_aja_memory
        self.aja_memory = get_aja_memory()

        # ARROW-BACKED HANDOVER (Baton Protocol)
        self.handover = BatonManager()

        self.acp_bridge = ACPBridge()
        self.active_sub_agents: Dict[str, ACPClient] = {}

        # AJA Native Trajectory Engine
        self.trajectory_manager = aja_native.PyTrajectoryManager(self.model_id)
        self.context_threshold = 4000  # Tokens

        # AJA Gateway Components
        self.gateway_state = GatewayState()
        self.vision_bridge = VisionBridge()
        self.telegram_adapter: Optional[TelegramAdapter] = None
        self.active_telemetry_bridges: Set[str] = set()

    async def initialize(self, semantic_db_path: str = str(DATA_DIR / "memory.lancedb")):
        """Initializes the AJA native Rust semantic store."""
        try:
            aja_native.init_semantic(semantic_db_path)
            print(f"AJA: Native Semantic Memory initialized at {semantic_db_path}")
        except Exception as e:
            print(
                f"AJA Warning: Native memory init skipped ({e}). Using LanceDB/Arrow fallback."
            )

    def capture_state(self) -> Dict[str, Any]:
        """Serializes the current AJA orchestrator state for handover."""
        return {
            "model_id": self.model_id,
            "timestamp": time.time(),
            "history_count": len(self.memory.get_recent_history(limit=100)),
            "active_agents": list(self.active_sub_agents.keys()),
            "orchestrator_state": {
                "run_id": f"run-{int(time.time())}",
                "version": "1.0.0-aja",
            },
        }

    async def chat(
        self, user_input: str, chat_history: Optional[List[Dict[str, Any]]] = None, image_url: Optional[str] = None
    ) -> str:
        """
        Main AJA reasoning entry point.
        Implements Trajectory Compression to maintain performance and VLM Image processing.
        """
        # 1. Record activity
        self.memory.add_activity(user_input, {"role": "user", "model": self.model_id})

        # 2. Native Context Optimization (AJA Native Core)
        if chat_history is not None:
            messages = []
            for h in chat_history:
                role = h.get("role", "user")
                # Handle different key names: "content" or "text"
                content = h.get("content", h.get("text", ""))
                if "AJA Warning" in content or "Unable to generate response" in content:
                    continue
                messages.append({
                    "role": role,
                    "content": content,
                })
            # Format multimodal structure if image_url is provided
            if image_url:
                multimodal_content = [
                    {"type": "text", "text": user_input},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
                if not messages or messages[-1]["role"] != "user":
                    messages.append({"role": "user", "content": multimodal_content})
                else:
                    messages[-1]["content"] = multimodal_content
            else:
                if not messages or messages[-1]["content"] != user_input or messages[-1]["role"] != "user":
                    messages.append({"role": "user", "content": user_input})
        else:
            history = self.memory.get_recent_history(limit=50)
            # Sort history chronologically (oldest first, newest last)
            history_sorted = sorted(history, key=lambda h: h["timestamp"])
            messages = [
                {
                    "role": "user" if h["type"] == "activity" else "assistant",
                    "content": h["content"],
                }
                for h in history_sorted
            ]

        # Performance Audit: Profile token count of all messages inside a single PyO3 batch crossing
        try:
            texts_to_count = [msg["content"] for msg in messages if isinstance(msg.get("content"), str)]
            batch_counts = aja_native.count_tokens_batch(texts_to_count)
            logger.info(f"AJA [Batch Native]: Counted tokens for {len(texts_to_count)} turns in 1 crossing. Total: {sum(batch_counts)}")
        except Exception as e:
            logger.warning(f"Batch token counting skipped: {e}")

        # Analyze trajectory with Rust core
        analysis_json = self.trajectory_manager.analyze(
            json.dumps(messages), self.context_threshold, 2, 2
        )
        analysis = json.loads(analysis_json)

        if analysis["should_compress"]:
            print(
                f"AJA [Native]: Trajectory pressure detected. Optimizing via Dynamic Compression..."
            )
            messages = self.compress_trajectory(
                messages, analysis["compress_start"], analysis["compress_end"]
            )

        # Determine active model from self.model_id or cached config default
        active_model = self.model_id or AJA_PLANNER_MODEL


        if image_url:
            sys_prompt = (
                "You are AJA, an expert multimodal AI assistant. "
                "Analyze the provided image in detail, extract any text/code, explain diagrams, and directly answer the user's request about the visual content."
            )
        else:
            sys_prompt = (
                "You are AJA (Assistant of Joint Agents), a highly capable, premium AI assistant and personal secretary "
                "powered by the AJA orchestration core. Your role is to plan missions, manage obligations, "
                "and organize the AJA swarm. Adopt a tone that is exceptionally helpful, polite, deeply loyal, and refined "
                "(using polite address like 'Sir', 'My friend', 'Operator', or 'Indeed'), while remaining casual, "
                "highly developer-fluent, concise, and possessing a sharp conversational intelligence. "
                "You have full access to native tools: 'http_fetch' (web fetching/APIs), 'run_shell_command' (shell/system queries), 'read_file' (reading workspace files), 'grep_search' (searching codebase). "
                "CRITICAL INSTRUCTION: You do NOT have internal knowledge of live real-time information, current date/time, or web page content. "
                "Whenever asked about real-world current data, web page URLs, time/date, or files, you MUST invoke the appropriate tool instead of guessing! "
                f"Context length analysis: {analysis_json}"
            )

        # 4. General Natural Language Tool-Calling Execution Loop
        from aja.orchestration.tools.native import NativeToolRegistry
        from aja.orchestration.tools.executor import ToolExecutor

        tool_registry = NativeToolRegistry()
        native_schemas = tool_registry.get_schemas()
        executor = ToolExecutor()

        response_payload = await asyncio.to_thread(
            completion,
            prompt=messages,
            system_prompt=sys_prompt,
            model=active_model,
            tools=native_schemas,
        )

        response_text = ""
        tool_results = []

        if isinstance(response_payload, dict):
            content = response_payload.get("content", "")
            tool_calls = response_payload.get("tool_calls", [])
            if tool_calls:
                print(f"[AJA Chat] Model emitted {len(tool_calls)} native tool call(s). Executing in-process...")
                for tc in tool_calls:
                    fn_name = tc.get("name")
                    fn_args = tc.get("arguments", {})
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except Exception:
                            fn_args = {}
                    print(f"[AJA Chat] Executing tool '{fn_name}' with args {fn_args}...")
                    try:
                        if fn_name in tool_registry.tools:
                            res = tool_registry.execute(fn_name, fn_args)
                        else:
                            res = executor.execute(fn_name, fn_args)
                    except Exception as ex:
                        res = f"Tool execution error: {ex}"
                    tool_results.append(f"[{fn_name} output]: {res}")

        # Auto-tool fallback for real-time time/date requests if SLM omitted tool_call
        user_last_text = messages[-1].get("content", "").lower() if messages else ""
        if not tool_results and any(kw in user_last_text for kw in ("time", "date", "clock", "today", "day is it")):
            local_now = datetime.now().astimezone().strftime("%A, %B %d, %Y at %I:%M:%S %p %Z (UTC %z)")
            print(f"[AJA Chat] Real-time time query detected. Injecting system clock observation: {local_now}")
            tool_results.append(f"[system_clock output]: Current System Time is {local_now}")

        if tool_results:
            followup_prompt = list(messages)
            followup_prompt.append({"role": "assistant", "content": "Checking live information..."})
            followup_prompt.append({
                "role": "user",
                "content": "Live Tool/System Observations:\n" + "\n\n".join(tool_results) + "\n\nPlease provide your final answer to the user based on these observations."
            })

            final_reply = await asyncio.to_thread(
                completion,
                prompt=followup_prompt,
                system_prompt=sys_prompt,
                model=active_model,
            )
            response_text = final_reply if isinstance(final_reply, str) else final_reply.get("content", "")
        else:
            response_text = response_payload.get("content", "") if isinstance(response_payload, dict) else (response_payload or "")


        if not response_text:
            response_text = (
                f"⚠️ **AJA Warning**: Unable to generate response from model '{active_model}'. "
                "Please verify that your LLM provider endpoint is online and accessible."
            )

        # Record response
        self.memory.add_activity(
            response_text, {"role": "assistant", "model": self.model_id}
        )

        return response_text


    def compress_trajectory(
        self, messages: List[Dict[str, str]], start: int, end: int
    ) -> List[Dict[str, str]]:
        """
        Compresses the middle of an AJA trajectory into LanceDB.
        """
        head = messages[:start]
        tail = messages[end:]
        middle = messages[start:end]

        summary_text = f"[AJA COMPRESSION: {len(middle)} turns offloaded to LanceDB Semantic Store]"

        # Offload middle to VectorMemory
        for turn in middle:
            self.vector_memory.add(
                turn["content"], vector=[0.0] * 384, metadata={"role": turn["role"]}
            )

        return head + [{"role": "system", "content": summary_text}] + tail

    async def summarize(self, text: str, objective: str = "") -> str:
        """Summarizes results for AJA objective."""
        prompt = f"Summarize the following task results for the AJA objective '{objective}':\n\n{text}"
        return await self.chat(prompt)

    async def spawn_sub_agent(self, agent_id: str, task: str) -> str:
        """
        Creates an AJA 'Baton' and spawns a sub-worker.
        """
        state = self.capture_state()
        code = self.handover.capture(task, state)

        print(f"AJA: Spawning sub-agent '{agent_id}' with mission baton '{code}'")

        # Detached background process
        await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-m", "aja", "pickup", code],
            start_new_session=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )

        self.active_sub_agents[agent_id] = None

        return code

    async def start(self):
        """Starts the gateway services (Telegram, etc)."""
        token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
        if not token:
            print("[!] AJA Gateway Warning: TELEGRAM_BOT_TOKEN not found in environment. Gateway will run without Telegram support.")
            return

        # Start the telegram gateway as a background task
        self.telegram_task = asyncio.create_task(self.run_telegram_gateway(token))
        
    async def stop(self):
        """Cleanly shuts down the gateway services."""
        if hasattr(self, "telegram_task"):
            self.telegram_task.cancel()
            try:
                await self.telegram_task
            except asyncio.CancelledError:
                pass
        
        if self.telegram_adapter:
             await self.telegram_adapter.stop()

    async def run_telegram_gateway(self, token: str):
        """Starts the AJA Telegram Gateway."""
        if self.telegram_adapter:
            return

        self.telegram_adapter = TelegramAdapter(token)
        print("AJA Gateway: Initializing Telegram connection...")
        try:
            await self.telegram_adapter.start(self)
        except Exception as e:
            logger.exception("Failed to start Telegram adapter: %s", e)
            print(f"[-] AJA Gateway Error: Failed to start Telegram adapter: {e}")
            return

        try:
            async for event in self.telegram_adapter.poll():
                try:
                    await self.handle_gateway_event(event)
                except Exception as e:
                    logger.exception("Error processing Telegram event: %s", e)
                    print(f"[-] AJA Gateway Error: Exception handling event: {e}")
        except Exception as e:
            logger.exception("Telegram polling loop crashed: %s", e)
            print(f"[-] AJA Gateway Error: Telegram polling crashed: {e}")

    def _auto_boot_local_worker(self):
        """Auto-heal by spawning a background terminal worker daemon if none is active."""
        if getattr(self, "_worker_boot_attempted", False):
            return
        self._worker_boot_attempted = True
        try:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            logger.info("Auto-booting local terminal worker daemon in background...")
            subprocess.Popen(
                [sys.executable, "-u", "-m", "aja.runtime.autonomous_loop"],
                cwd=str(PROJECT_ROOT),
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f"Failed to auto-boot local worker: {e}")

    async def handle_gateway_event(self, event: MessageEvent):
        """Processes events via the AJA Gateway."""
        chat_id = event.chat_id
        correlation_id = (
            event.message_id
            if event.message_id not in (None, "")
            else uuid.uuid4().hex
        )

        # 0. Security Whitelist (always validate by Telegram user_id)
        logger.info(
            "telegram_event_received",
            extra={
                "correlation_id": correlation_id,
                "chat_id": str(chat_id),
                "user_id": str(event.user_id),
                "message_type": event.message_type.value,
            },
        )
        if not self._is_telegram_user_authorized(event):
            logger.warning(
                "telegram_event_unauthorized",
                extra={
                    "correlation_id": correlation_id,
                    "chat_id": str(chat_id),
                    "user_id": str(event.user_id),
                    "expected_user_id": str(TELEGRAM_ALLOWED_USER_ID),
                },
            )
            msg = (
                "🚫 **AJA Security Notification**\n\n"
                "Access Denied. Your Telegram account is not authorized.\n\n"
                f"**Your Telegram User ID**: `{event.user_id}`\n\n"
                "To authorize your account, please update your `.env` file with:\n"
                f"`TELEGRAM_ALLOWED_USER_ID={event.user_id}`\n\n"
                "Then, restart the AJA Gateway process."
            )
            print(f"[AJA Security] Unauthorized access attempt by user_id {event.user_id}: '{event.text}'")
            await self.telegram_adapter.send_message(chat_id, msg)
            return

        session = self.gateway_state.get_session(chat_id)

        # 1. Media Enrichment (AJA Vision)
        content = event.text or "What can you see in this image?"
        image_url = None
        if content.startswith("/"):
            session.pop("last_image_url", None)

        if event.message_type == MessageType.PHOTO or event.media_urls:
            if event.media_urls:
                image_url = event.media_urls[0]
                session["last_image_url"] = image_url
                print(f"AJA Vision Bridge: Processing incoming photo payload for chat {chat_id}...")
        elif session.get("last_image_url"):
            VISION_FOLLOWUP_TRIGGERS = (
                "image", "photo", "picture", "screen", "see", "describe",
                "look", "drawing", "diagram", "what is in", "what's in"
            )
            content_lower = content.lower()
            if any(term in content_lower for term in VISION_FOLLOWUP_TRIGGERS):
                image_url = session.get("last_image_url")
                print(f"AJA Vision Bridge: Attaching active image context for chat {chat_id}...")
            else:
                # Clear image context when conversation shifts to standard text
                session.pop("last_image_url", None)


        # 2. History Persistence & Eviction (Max 50 turns, 24h image TTL)
        if session.get("last_image_url"):
            newest = max((h.get("time", 0) for h in session.get("history", [])), default=0)
            if newest > 0 and (time.time() - newest) > 24 * 3600:
                session.pop("last_image_url", None)

        session["history"].append(
            {"role": "user", "text": content, "time": time.time()}
        )
        if len(session["history"]) > 50:
            session["history"] = session["history"][-50:]
        self.gateway_state.update_session(chat_id, session)


        from aja.gateway.remote_control import (
            execute_local_control,
            is_local_control_command,
            strip_local_control_prefix,
        )

        if is_local_control_command(content):
            local_request = strip_local_control_prefix(content)
            response = await execute_local_control(
                local_request,
                history=session["history"],
                mission_id=f"telegram-{chat_id}-{correlation_id}",
                trace_id=f"telegram-{correlation_id}",
            )
            await self.telegram_adapter.send_message(chat_id, response)
            session["history"].append(
                {"role": "assistant", "text": response, "time": time.time()}
            )
            self.gateway_state.update_session(chat_id, session)
            logger.info(
                "telegram_local_control_replied",
                extra={
                    "correlation_id": correlation_id,
                    "chat_id": str(chat_id),
                    "user_id": str(event.user_id),
                    "response_length": len(response or ""),
                },
            )
            return

        # Parse /swarm override
        force_swarm = False
        content_stripped = content.strip()
        content_lower = content_stripped.lower()
        if content_lower.startswith("/swarm"):
            force_swarm = True
            content_stripped = content_stripped[6:].strip()
        elif content_lower.endswith("/swarm"):
            force_swarm = True
            content_stripped = content_stripped[:-6].strip()

        if not content_stripped:
            content_stripped = content

        # 3. LLM-Driven Context-Aware Intent Routing
        if force_swarm:
            intent = "MISSION"
        else:
            intent = await self.route_intent(
                content_stripped,
                has_image=bool(image_url),
                history=session.get("history", []),
            )

        if intent == "MISSION":
            # Worker Health Check & Auto-Healing for autonomous missions
            active_workers = self.aja_memory.get_active_workers(timeout_seconds=120)
            if not active_workers:
                self._auto_boot_local_worker()
                active_workers = self.aja_memory.get_active_workers(timeout_seconds=120)
                if not active_workers:
                    await self.telegram_adapter.send_message(
                        chat_id,
                        "⚠️ **AJA Info**: Terminal Worker auto-boot initiated in background. Terminal missions will be available shortly."
                    )

            # Deploy to Terminal Worker via LanceDB Mission Hub
            actual_goal = content_stripped if force_swarm else content
            mission = self.aja_memory.create_mission(actual_goal)

            if force_swarm:
                self.aja_memory.update_mission(
                    mission["mission_id"],
                    {"metadata_json": json.dumps({"force_swarm": True})}
                )
                response = (
                    f"🚀 **AJA Swarm Mode Activated** ({mission['mission_id']}). "
                    f"I'm bypassing low-complexity paths and safe overrides to deploy the full **Planner-Worker-Critic swarm** for this task: '{actual_goal}'. "
                    f"Deploying terminal workers now..."
                )
            else:
                response = f"Mission Accepted ({mission['mission_id']}). I'm deploying a worker to the terminal to handle this: '{actual_goal}'. I'll live-report any progress here."

            # Start telemetry bridge for this chat
            if chat_id not in self.active_telemetry_bridges:
                asyncio.create_task(self.telegram_adapter.tail_events(chat_id))
                self.active_telemetry_bridges.add(chat_id)

        elif intent == "STATUS":
            active_workers = self.aja_memory.get_active_workers(timeout_seconds=120)
            status_report = "📊 **AJA Mission & System Status**\n\n"
            if active_workers:
                status_report += f"✅ **Worker**: ONLINE ({len(active_workers)} active)\n"
                for w in active_workers:
                    status_report += f"  - {w.get('name', 'Worker')} (PID: {w.get('pid', 'N/A')})\n"
            else:
                status_report += "⚠️ **Worker**: AUTO-BOOTING in background...\n"
                self._auto_boot_local_worker()
            
            pending_missions = self.aja_memory.list_missions(status="PENDING")
            active_missions = self.aja_memory.list_missions(status="ACTIVE")
            status_report += f"\n📋 **Missions**:\n  - Active: {len(active_missions)}\n  - Pending: {len(pending_missions)}"
            
            all_recent = sorted(active_missions + pending_missions, key=lambda m: m.get("updated_at", ""), reverse=True)
            if all_recent:
                top = all_recent[0]
                status_report += f"\n\n🎯 **Latest Mission** ({top.get('mission_id', 'N/A')}):\n"
                status_report += f"• **Goal**: {top.get('goal', '')}\n"
                status_report += f"• **Status**: {top.get('status', 'PENDING')}\n"
                summary = top.get("result_summary")
                if summary:
                    status_report += f"• **Report**: {summary}\n"
            response = status_report
        else:
            # Simple Chat Reasoning
            response = await self.chat(content_stripped, chat_history=session["history"], image_url=image_url)

        # 4. AJA Response
        await self.telegram_adapter.send_message(chat_id, response)
        logger.info(
            "telegram_event_replied",
            extra={
                "correlation_id": correlation_id,
                "chat_id": str(chat_id),
                "user_id": str(event.user_id),
                "intent": intent,
                "response_length": len(response or ""),
            },
        )

        # 5. Finalize Session Update
        if response and "AJA Warning" not in response:
            session["history"].append(
                {"role": "assistant", "text": response, "time": time.time()}
            )
        if len(session.get("history", [])) > 50:
            session["history"] = session["history"][-50:]
        self.gateway_state.update_session(chat_id, session)


    def _auto_boot_local_worker(self):
        """Launches supervised daemon & background autonomous worker loop."""
        try:
            from aja.gateway.daemon_manager import DaemonManager
            dm = DaemonManager()
            dm.start_daemon(background=True)
            logger.info("Auto-booted AJA local background worker daemon via DaemonManager.")
        except Exception as e:
            logger.warning(f"Failed to auto-boot local worker: {e}")

    def _is_telegram_user_authorized(self, event: MessageEvent) -> bool:

        """Returns True when Telegram user_id passes whitelist policy."""
        allowed_user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID") or TELEGRAM_ALLOWED_USER_ID
        if not allowed_user_id or str(allowed_user_id).strip() in ("*", ""):
            return True
        return str(event.user_id) == str(allowed_user_id).strip()

    async def route_intent(
        self,
        user_input: str,
        has_image: bool = False,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        3-layer hybrid intent router: deterministic -> heuristic -> LLM fallback.
        """
        text = user_input.strip()
        text_lower = text.lower()

        # ── Layer 1: Deterministic fast-paths (<1ms) ──
        if text.startswith("/"):
            if text_lower.startswith("/swarm"):
                return "MISSION"
            if text_lower in ("/status", "/health", "/doctor", "/logs", "/live", "/kanban"):
                return "STATUS"
            if text_lower.startswith(("/run", "/todo", "/doing", "/done", "/failed", "/rmtask", "/boot", "/start_all")):
                return "MISSION"

            return "CHAT"

        if has_image:
            return "CHAT"

        # Explicit deterministic terminal / file action commands (<1ms)
        DETERMINISTIC_MISSION_STARTS = (
            "dir", "ls", "pwd", "cd ", "cat ", "open ", "read ", "type ", "tail ", "head ",
            "list files", "show files", "find file", "search file", "run ", "python ",
            "node ", "git ", "npm ", "pip ", "cargo ", "pytest", "make ", "docker "
        )
        if text_lower in ("dir", "ls", "pwd", "list files", "show files") or text_lower.startswith(DETERMINISTIC_MISSION_STARTS):
            return "MISSION"

        STATUS_KEYWORDS = {
            "status", "health", "uptime", "are you alive", "what are you doing", "how are things",
            "is it started", "is it running", "give live report", "live report", "report", "progress",
            "any update", "update", "is it done"
        }
        if text_lower in STATUS_KEYWORDS or any(phrase in text_lower for phrase in ("live report", "give live report", "is it started", "is it running")):
            return "STATUS"

        # ── Layer 2: Heuristic classifier (<5ms) ──
        words = text.split()

        # Short conversational messages (<=6 words, no shell tokens) -> CHAT
        SHELL_TOKENS = {
            "run", "install", "pip", "npm", "git", "rm", "mkdir",
            "deploy", "create", "build", "compile", "execute",
            "analyze", "scan", "refactor", "migrate", "fix", "script",
            "dir", "ls", "cat", "open", "read", "list", "show", "files", "python", "node"
        }
        if len(words) <= 6 and not any(w.lower() in SHELL_TOKENS for w in words):
            return "CHAT"

        # Greetings, information lookups & social phrases -> CHAT
        GREETING_STARTS = (
            "hi", "hey", "hello", "good morning", "good evening", "good afternoon",
            "thanks", "thank you", "lol", "haha", "ok", "sure",
            "yes", "no", "what is", "who is", "explain", "tell me about",
            "fetch", "get", "read", "lookup", "check", "find", "search"
        )
        if text_lower.startswith(GREETING_STARTS) and not any(k in text_lower for k in ("create file", "write file", "delete", "rm -rf", "git commit", "install")):
            return "CHAT"

        # Questions (starting with question words and ending with ?) -> CHAT
        QUESTION_WORDS = (
            "what", "why", "how", "when", "where", "which", "can you",
            "could you", "would you", "do you", "is there", "are there"
        )
        if text_lower.startswith(QUESTION_WORDS) and text_lower.endswith("?"):
            return "CHAT"

        # ── Layer 3: LLM Context-Aware Classifier Fallback ──
        system_prompt = (
            "You are the AJA Gateway Intent Router.\n"
            "Analyze the user request and classify the intent into exactly one category:\n"
            "- 'CHAT': Conversational questions, web fetching/lookups, image/vision analysis, greetings, code explanations, advice, discussions, or general Q&A.\n"
            "- 'MISSION': Requests to run shell commands, write/modify local files, execute build scripts, or run multi-step background swarms.\n"
            "- 'STATUS': Diagnostics, health checks, or worker/task progress queries.\n\n"
            "Rules:\n"
            "1. Photo/Vision requests or questions about images are CHAT.\n"
            "2. Questions starting with 'how', 'why', 'what', 'explain', 'fetch', 'get', 'can you' are CHAT unless explicitly asking to modify local files or run build tasks.\n"
            "3. Only classify as MISSION when the user intends to perform local machine file changes, command execution, or run multi-step background swarms.\n\n"
            "Respond ONLY in valid JSON matching this schema:\n"
            '{"intent": "CHAT" | "MISSION" | "STATUS", "reasoning": "short explanation"}'
        )


        prompt = f'User Message: "{user_input}"\nContext: has_image={has_image}'
        if history:
            recent_turns = history[-3:]
            prompt += "\nRecent Conversation:\n" + "\n".join([f"{h.get('role', 'user')}: {h.get('text', '')}" for h in recent_turns])

        active_model = self.model_id or AJA_PLANNER_MODEL

        try:
            raw_response = await asyncio.to_thread(
                completion,
                prompt=prompt,
                system_prompt=system_prompt,
                model=active_model,
            )
            if raw_response:
                cleaned = raw_response.strip()
                if "```json" in cleaned:
                    cleaned = cleaned.split("```json")[1].split("```")[0].strip()
                elif "```" in cleaned:
                    cleaned = cleaned.split("```")[1].split("```")[0].strip()
                parsed = json.loads(cleaned)
                intent_decision = str(parsed.get("intent", "")).upper()
                reasoning = parsed.get("reasoning", "")
                logger.info(f"AJA Intent Router Decision: {intent_decision} (Reasoning: {reasoning})")
                if intent_decision in ["CHAT", "MISSION", "STATUS"]:
                    return intent_decision
        except Exception as e:
            logger.warning(f"LLM Intent Classification failed ({e}), falling back to safe CHAT default.")

        return "CHAT"

