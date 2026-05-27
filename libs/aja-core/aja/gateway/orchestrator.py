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
from aja.config import PROJECT_ROOT, DATA_DIR, TELEGRAM_ALLOWED_USER_ID
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

    def __init__(self, model_id: str = "claude-3-5-sonnet"):
        self.model_id = model_id
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
        self.trajectory_manager = aja_native.PyTrajectoryManager(model_id)
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
        self, user_input: str, chat_history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Main AJA reasoning entry point.
        Implements Trajectory Compression to maintain performance.
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
                messages.append({
                    "role": role,
                    "content": content,
                })
            # Ensure the latest user_input is also included if not already at the end
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

        # 3. Perform the actual chat call
        # AJA persona is used for the conversational layer
        response_text = completion(
            prompt=messages,
            system_prompt=(
                "You are AJA (Assistant of Joint Agents), a highly capable, premium hacker-butler and personal secretary "
                "powered by the AJA orchestration core. Your role is to plan missions, manage obligations, "
                "and organize the AJA swarm. Adopt a tone that is exceptionally helpful, polite, deeply loyal, and refined "
                "(using polite address like 'Sir', 'My friend', 'Operator', or 'Indeed'), while remaining casual, "
                "highly developer-fluent, concise, and possessing a sharp, 'hacker-elite' conversational intelligence. "
                "Always present clean briefs, summarize tasks, manage meetings/obligations, and coordinate swarms proactively. "
                f"Context length analysis: {analysis_json}"
            ),
            model=self.model_id,
        )

        if not response_text:
            response_text = f"AJA [Runtime Warning]: Mission reasoning failed for '{user_input}'. Check gateway logs."

        # 4. Record response
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
        content = event.text
        if event.message_type == MessageType.PHOTO:
            if event.raw_event and event.raw_event.message.photo:
                print(f"AJA: Enriching mission context via Vision Bridge...")
                # ... Vision logic ...

        # 2. History Persistence
        session["history"].append(
            {"role": "user", "text": content, "time": time.time()}
        )
        self.gateway_state.update_session(chat_id, session)

        # 3. AJA Reasoning
        # 2.5 Worker Health Check
        active_workers = self.aja_memory.get_active_workers(timeout_seconds=120)
        if not active_workers and content.lower() != "status":
            await self.telegram_adapter.send_message(
                chat_id, 
                "⚠️ **AJA Warning**: The Terminal Worker appears to be offline. I can chat, but I won't be able to execute terminal missions until it is restarted."
            )
        
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
            
        # 3. Hybrid Intent Routing
        if force_swarm:
            intent = "MISSION"
        else:
            intent = await self.route_intent(content_stripped)
        
        if intent == "MISSION":
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
            status_report = "📊 **AJA System Status**\n\n"
            if active_workers:
                status_report += f"✅ **Worker**: ONLINE ({len(active_workers)} active)\n"
                for w in active_workers:
                    status_report += f"  - {w['name']} (PID: {w['pid']})\n"
            else:
                status_report += "❌ **Worker**: OFFLINE\n"
            
            pending_missions = self.aja_memory.list_missions(status="PENDING")
            active_missions = self.aja_memory.list_missions(status="ACTIVE")
            status_report += f"\n📋 **Missions**:\n  - Active: {len(active_missions)}\n  - Pending: {len(pending_missions)}"
            response = status_report
        else:
            # Simple Chat Reasoning
            response = await self.chat(content_stripped, chat_history=session["history"])

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
        session["history"].append(
            {"role": "assistant", "text": response, "time": time.time()}
        )
        self.gateway_state.update_session(chat_id, session)

    def _is_telegram_user_authorized(self, event: MessageEvent) -> bool:
        """Returns True when Telegram user_id passes whitelist policy."""
        if not TELEGRAM_ALLOWED_USER_ID:
            logger.critical("Security Configuration Error: TELEGRAM_ALLOWED_USER_ID is empty or missing! Authorization denied.")
            return False
        return str(event.user_id) == str(TELEGRAM_ALLOWED_USER_ID)

    async def route_intent(self, user_input: str) -> str:
        """
        Two-tiered intent router:
        Tier 1: High-speed deterministic command-parser. Handles slash commands, exact keywords.
        Tier 2: High-speed LLM classifier router. Used when deterministic parsing is ambiguous.
        """
        user_input_lower = user_input.lower().strip()
        
        # If input has /swarm tag, it's definitely a mission
        if "/swarm" in user_input_lower:
            return "MISSION"
        
        # --- TIER 1: Deterministic Parsing ---
        # Exact command matches
        if user_input_lower in ["status", "/status", "health", "are you alive"]:
            return "STATUS"
        if user_input_lower in ["dir", "ls"]:
            return "MISSION"
            
        # Slash commands
        if user_input_lower.startswith("/"):
            cmd = user_input_lower.split()[0]
            if cmd in ["/status", "/doctor", "/live", "/kanban"]:
                return "STATUS"
            elif cmd in ["/run", "/todo", "/doing", "/done", "/failed", "/rmtask", "/swarm"]:
                return "MISSION"
            else:
                return "CHAT"
                
        # Strict prefix command checks
        mission_prefixes = [
            "run ", "execute ", "install ", "scrape ", "audit ",
            "go ", "open ", "show ", "list ", "navigate ", "find ", "read ",
            "dir ", "ls "
        ]
        for prefix in mission_prefixes:
            if user_input_lower.startswith(prefix):
                return "MISSION"
                
        # --- TIER 2: High-Speed LLM Classifier Router ---
        # Use LLM classification for ambiguous inputs
        system_prompt = (
            "You are a high-speed routing classifier for AJA.\n"
            "Analyze the user's input and classify it into exactly one of these categories:\n"
            "- 'MISSION': The user wants the agent to perform an active task, search, code, write a script, scrape, execute shell commands, delete/create files, or perform background worker tasks.\n"
            "- 'STATUS': The user is asking about the agent's current state, health, tasks status, progress of active goals, or worker status.\n"
            "- 'CHAT': The user is just asking a question, greeting, making small talk, or expressing generic thoughts without asking the agent to perform a shell/system task.\n\n"
            "Response MUST be exactly one word: 'MISSION', 'STATUS', or 'CHAT'."
        )
        
        try:
            response = await asyncio.to_thread(
                completion,
                prompt=f"Classify this input:\n\"{user_input}\"",
                system_prompt=system_prompt
            )
            classification = response.strip().upper()
            if classification in ["MISSION", "STATUS", "CHAT"]:
                return classification
        except Exception as e:
            logger.error(f"LLM Intent Classifier Router failed: {e}")
            
        # Fallback to local heuristic checks if LLM fails
        mission_triggers = [
            "run", "find", "search", "scan", "execute", "delete", "remove", 
            "do ", "make ", "look for", "check", "install", "audit", "monitor",
            "scrape", "summarize file", "read ", "analyze"
        ]
        for trigger in mission_triggers:
            if user_input_lower.startswith(trigger):
                return "MISSION"
        
        action_keywords = ["search", "find", "list", "check", "run", "execute", "audit"]
        if any(word in user_input_lower for word in action_keywords) and len(user_input_lower.split()) > 4:
            return "MISSION"

        if len(user_input_lower.split()) > 12: 
             return "MISSION"
             
        return "CHAT"
