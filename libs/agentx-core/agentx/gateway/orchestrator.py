import os
import json
import asyncio
import time
import uuid
import logging
import subprocess
import sys
from typing import List, Dict, Any, Optional, Set
import agentx_native
from agentx.config import PROJECT_ROOT, TELEGRAM_ALLOWED_USER_ID
from agentx.runtime.memory import MemoryTree
from agentx.runtime.handover import BatonManager
from agentx.memory.vector import VectorMemory
from agentx.api.universal import UniversalRequest, UniversalItem, ContentBlock, Role
from agentx.api.acp import ACPBridge, ACPClient
from agentx.llm import completion
from agentx.gateway.tg_client import TelegramAdapter
from agentx.memory.secretary import AJAMemory
from agentx.gateway.persistence import GatewayState
from agentx.gateway.vision import VisionBridge
from agentx.gateway.base import MessageEvent, MessageType

logger = logging.getLogger(__name__)


class UnifiedGateway:
    """
    The main integration hub for AgentX.
    Combines high-performance orchestration core logic with the AJA Gateway.
    """

    def __init__(self, model_id: str = "claude-3-5-sonnet"):
        self.model_id = model_id
        self.memory = MemoryTree()

        # DUAL BRAIN: MemoryTree (Structured) + VectorMemory (LanceDB/Semantic)
        self.vector_memory = VectorMemory(table_name="mission_semantic")
        self.aja_memory = AJAMemory()

        # ARROW-BACKED HANDOVER (Baton Protocol)
        self.handover = BatonManager()

        self.acp_bridge = ACPBridge()
        self.active_sub_agents: Dict[str, ACPClient] = {}

        # AgentX Native Trajectory Engine
        self.trajectory_manager = agentx_native.PyTrajectoryManager(model_id)
        self.context_threshold = 4000  # Tokens

        # AJA Gateway Components
        self.gateway_state = GatewayState()
        self.vision_bridge = VisionBridge()
        self.telegram_adapter: Optional[TelegramAdapter] = None
        self.active_telemetry_bridges: Set[str] = set()

    async def initialize(self, semantic_db_path: str = "./.agentx/memory.lancedb"):
        """Initializes the AgentX native Rust semantic store."""
        try:
            agentx_native.init_semantic(semantic_db_path)
            print(f"AgentX: Native Semantic Memory initialized at {semantic_db_path}")
        except Exception as e:
            print(
                f"AgentX Warning: Native memory init skipped ({e}). Using LanceDB/Arrow fallback."
            )

    def capture_state(self) -> Dict[str, Any]:
        """Serializes the current AgentX orchestrator state for handover."""
        return {
            "model_id": self.model_id,
            "timestamp": time.time(),
            "history_count": len(self.memory.get_recent_history(limit=100)),
            "active_agents": list(self.active_sub_agents.keys()),
            "orchestrator_state": {
                "run_id": f"run-{int(time.time())}",
                "version": "1.0.0-agentx",
            },
        }

    async def chat(self, user_input: str) -> str:
        """
        Main AgentX reasoning entry point.
        Implements Trajectory Compression to maintain performance.
        """
        # 1. Record activity
        self.memory.add_activity(user_input, {"role": "user", "model": self.model_id})

        # 2. Native Context Optimization (AgentX Native Core)
        history = self.memory.get_recent_history(limit=50)
        messages = [
            {
                "role": "user" if h["type"] == "activity" else "assistant",
                "content": h["content"],
            }
            for h in history
        ]

        # Analyze trajectory with Rust core
        analysis_json = self.trajectory_manager.analyze(
            json.dumps(messages), self.context_threshold, 2, 2
        )
        analysis = json.loads(analysis_json)

        if analysis["should_compress"]:
            print(
                f"AgentX [Native]: Trajectory pressure detected. Optimizing via Dynamic Compression..."
            )
            messages = self.compress_trajectory(
                messages, analysis["compress_start"], analysis["compress_end"]
            )

        # 3. Perform the actual chat call
        # AJA persona is used for the conversational layer
        response_text = completion(
            prompt=user_input,
            system_prompt=(
                "You are AJA (Assistant of Joint Agents), a premium natural-language secretary powered by the AgentX orchestration core. "
                "Your role is to plan missions, manage obligations, and coordinate the AgentX swarm. "
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
        Compresses the middle of an AgentX trajectory into LanceDB.
        """
        head = messages[:start]
        tail = messages[end:]
        middle = messages[start:end]

        summary_text = f"[AGENTX COMPRESSION: {len(middle)} turns offloaded to LanceDB Semantic Store]"

        # Offload middle to VectorMemory
        for turn in middle:
            self.vector_memory.add(
                turn["content"], vector=[0.0] * 384, metadata={"role": turn["role"]}
            )

        return head + [{"role": "system", "content": summary_text}] + tail

    async def summarize(self, text: str, objective: str = "") -> str:
        """Summarizes results for AgentX objective."""
        prompt = f"Summarize the following task results for the AgentX objective '{objective}':\n\n{text}"
        return await self.chat(prompt)

    async def spawn_sub_agent(self, agent_id: str, task: str) -> str:
        """
        Creates an AgentX 'Baton' and spawns a sub-worker.
        """
        state = self.capture_state()
        code = self.handover.capture(task, state)

        print(f"AgentX: Spawning sub-agent '{agent_id}' with mission baton '{code}'")

        # Detached background process
        subprocess.Popen(
            [sys.executable, "-m", "agentx", "pickup", code],
            start_new_session=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )

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
        await self.telegram_adapter.start(self)

        async for event in self.telegram_adapter.poll():
            await self.handle_gateway_event(event)

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
        active_workers = self.aja_memory.get_active_workers()
        if not active_workers and content.lower() != "status":
            await self.telegram_adapter.send_message(
                chat_id, 
                "⚠️ **AJA Warning**: The Terminal Worker appears to be offline. I can chat, but I won't be able to execute terminal missions until it is restarted."
            )
        
        # 3. Hybrid Intent Routing
        intent = self.route_intent(content)
        
        if intent == "MISSION":
            # Deploy to Terminal Worker via LanceDB Mission Hub
            mission = self.aja_memory.create_mission(content)
            response = f"Mission Accepted ({mission['mission_id']}). I'm deploying a worker to the terminal to handle this: '{content}'. I'll live-report any progress here."
            
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
            response = await self.chat(content)

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
            return True
        return str(event.user_id) == str(TELEGRAM_ALLOWED_USER_ID)

    def route_intent(self, user_input: str) -> str:
        """
        Fast-path intent router. Returns 'MISSION', 'STATUS', or 'CHAT'.
        """
        user_input_lower = user_input.lower().strip()
        
        # System Commands
        if user_input_lower in ["status", "/status", "health", "are you alive"]:
            return "STATUS"
            
        # Regex/Keywords fast-path
        mission_triggers = [
            "run", "find", "search", "scan", "execute", "delete", "remove", 
            "do ", "make ", "look for", "check", "install", "audit", "monitor",
            "scrape", "summarize file", "read ", "analyze"
        ]
        for trigger in mission_triggers:
            if user_input_lower.startswith(trigger):
                return "MISSION"
        
        # Action-oriented phrasing (e.g. "could you please search...")
        action_keywords = ["search", "find", "list", "check", "run", "execute", "audit"]
        if any(word in user_input_lower for word in action_keywords) and len(user_input_lower.split()) > 4:
            return "MISSION"

        # Ambiguous check (long instructions are likely missions)
        if len(user_input_lower.split()) > 12: 
             return "MISSION"
             
        return "CHAT"
