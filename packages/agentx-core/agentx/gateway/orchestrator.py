import os
import json
import asyncio
import time
import subprocess
import sys
from typing import List, Dict, Any, Optional
import agentx_native
from agentx.config import PROJECT_ROOT, TELEGRAM_ALLOWED_USER_ID
from agentx.runtime.memory import MemoryTree
from agentx.runtime.handover import BatonManager
from agentx.memory.vector import VectorMemory
from agentx.api.universal import UniversalRequest, UniversalItem, ContentBlock, Role
from agentx.api.acp import ACPBridge, ACPClient
from agentx.llm import completion
from agentx.gateway.telegram import TelegramAdapter
from agentx.gateway.persistence import GatewayState
from agentx.gateway.vision import VisionBridge
from agentx.gateway.base import MessageEvent, MessageType


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
                turn["content"], vector=[0.0] * 1536, metadata={"role": turn["role"]}
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

    async def run_telegram_gateway(self, token: str):
        """Starts the AJA Telegram Gateway."""
        if self.telegram_adapter:
            return

        self.telegram_adapter = TelegramAdapter(token)
        print("AJA Gateway: Initializing Telegram connection...")

        async for event in self.telegram_adapter.poll():
            await self.handle_gateway_event(event)

    async def handle_gateway_event(self, event: MessageEvent):
        """Processes events via the AJA Gateway."""
        chat_id = event.chat_id

        # 0. Security Whitelist
        if TELEGRAM_ALLOWED_USER_ID and str(chat_id) != str(TELEGRAM_ALLOWED_USER_ID):
            print(
                f"AJA Warning: Unauthorized access attempt from chat_id {chat_id}"
            )
            return

        session = self.gateway_state.get_session(chat_id)

        # 1. Media Enrichment (AJA Vision)
        content = event.text
        if event.type == MessageType.PHOTO:
            if event.metadata and "file_id" in event.metadata:
                print(f"AJA: Enriching mission context via Vision Bridge...")
                content = await self.vision_bridge.describe_image(b"")

        # 2. History Persistence
        session["history"].append(
            {"role": "user", "text": content, "time": time.time()}
        )
        self.gateway_state.update_session(chat_id, session)

        # 3. AJA Reasoning
        await self.telegram_adapter.send_notification(
            chat_id, "Planning...", importance="low"
        )
        response = await self.chat(content)

        # 4. AJA Response
        await self.telegram_adapter.send_message(chat_id, response)

        # 5. Finalize Session Update
        session["history"].append(
            {"role": "assistant", "text": response, "time": time.time()}
        )
        self.gateway_state.update_session(chat_id, session)
