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
from agentx.gateway.telegram import TelegramAdapter
from agentx.gateway.persistence import GatewayState
from agentx.gateway.vision import VisionBridge
from agentx.gateway.base import MessageEvent, MessageType


class UnifiedGateway:
    """
    The main integration hub for AgentX.
    Combines high-performance translation, Unified Arrow Memory (LanceDB),
    and modular multi-agent orchestration.
    """

    def __init__(self, model_id: str = "claude-3-5-sonnet"):
        self.model_id = model_id
        self.memory = MemoryTree()

        # DUAL BRAIN: MemoryTree (Structured) + VectorMemory (LanceDB/Semantic)
        self.vector_memory = VectorMemory(table_name="mission_semantic")

        # ARROW-BACKED HANDOVER
        self.handover = BatonManager()

        self.acp_bridge = ACPBridge()
        self.active_sub_agents: Dict[str, ACPClient] = {}

        # Native Trajectory Engine for local model protection
        self.trajectory_manager = agentx_native.PyTrajectoryManager(model_id)
        self.context_threshold = 4000  # Tokens

        # AJA GATEWAY COMPONENTS
        self.gateway_state = GatewayState()
        self.vision_bridge = VisionBridge()
        self.telegram_adapter: Optional[TelegramAdapter] = None

    async def initialize(self, semantic_db_path: str = "./.agentx/memory.lancedb"):
        """Initializes the native Rust semantic store."""
        try:
            agentx_native.init_semantic(semantic_db_path)
            print(f"AgentX: Native Semantic Memory initialized at {semantic_db_path}")
        except Exception as e:
            print(
                f"AgentX Warning: Native memory init skipped ({e}). Using LanceDB/Arrow fallback."
            )

    def capture_state(self) -> Dict[str, Any]:
        """Serializes the current orchestrator state for handover."""
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
        Main reasoning entry point.
        Implements Trajectory Compression to maintain performance on cheap hardware.
        """
        # 1. Record activity in both brains
        self.memory.add_activity(user_input, {"role": "user", "model": self.model_id})
        # Note: Vector memory add would typically happen after embedding

        # 2. Native Context Optimization (AgentX Native Optimization)
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
                f"AgentX [Native]: Pressure detected. Optimizing trajectory via Dynamic Compression..."
            )
            # Perform compression (summarization and offloading to LanceDB)
            messages = self.compress_trajectory(
                messages, analysis["compress_start"], analysis["compress_end"]
            )

        # 3. Build Universal Request payload
        universal_input = []
        for msg in messages:
            universal_input.append(
                UniversalItem(
                    type="message",
                    role=Role.USER if msg["role"] == "user" else Role.ASSISTANT,
                    content=[ContentBlock(type="text", text=msg["content"])],
                )
            )

        request = UniversalRequest(
            model=self.model_id,
            input=universal_input,
            instructions=[
                ContentBlock(
                    type="text",
                    text="You are AgentX, a high-leverage autonomous orchestrator.",
                )
            ],
        )

        # 4. Use Native Core for high-perf translation
        request_json = request.model_dump_json()
        translated_json = agentx_native.translate_to_anthropic(request_json)

        # 5. Perform the actual chat call (Simulation)
        # In a real mission, this would call the configured provider
        response_text = f"AgentX [Rust-Core Integrated]: Reasoning about '{user_input}'"

        # 6. Record response
        self.memory.add_activity(
            response_text, {"role": "assistant", "model": self.model_id}
        )

        return response_text

    def compress_trajectory(
        self, messages: List[Dict[str, str]], start: int, end: int
    ) -> List[Dict[str, str]]:
        """
        Compresses the middle of a trajectory.
        Offloads the compressed content to LanceDB for zero-copy retrieval.
        """
        head = messages[:start]
        tail = messages[end:]
        middle = messages[start:end]

        summary_text = f"[AGENTX COMPRESSION: {len(middle)} turns offloaded to LanceDB Semantic Store]"

        # Offload middle to VectorMemory
        for turn in middle:
            # Simplified: In reality, we'd embed turn['content'] here
            self.vector_memory.add(
                turn["content"], vector=[0.0] * 1536, metadata={"role": turn["role"]}
            )

        return head + [{"role": "system", "content": summary_text}] + tail

    async def summarize(self, text: str, objective: str = "") -> str:
        """Summarizes large text blobs to stay under the 'Latency Wall'."""
        prompt = f"Summarize the following task results for the objective '{objective}':\n\n{text}"
        return await self.chat(prompt)

    async def spawn_sub_agent(self, agent_id: str, task: str) -> str:
        """
        Creates an ARROW-BACKED 'Baton' and spawns a sub-worker via CLI.
        This enables zero-copy state handoff across the swarm.
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
        print("AJA: Starting Telegram Gateway...")
        
        async for event in self.telegram_adapter.poll():
            await self.handle_gateway_event(event)

    async def handle_gateway_event(self, event: MessageEvent):
        """Processes events from the AJA Gateway."""
        chat_id = event.chat_id
        
        # 0. Security Whitelist
        if TELEGRAM_ALLOWED_USER_ID and str(chat_id) != str(TELEGRAM_ALLOWED_USER_ID):
            print(f"AJA Warning: Unauthorized access attempt from chat_id {chat_id}")
            # Optional: send a polite rejection message
            # await self.telegram_adapter.send_message(chat_id, "I am AJA, a personal assistant. I only respond to my authorized operator.")
            return

        session = self.gateway_state.get_session(chat_id)
        
        # 1. Enrich event if it has media
        content = event.text
        if event.type == MessageType.PHOTO:
            # We assume the adapter put the file_id or download_url in event.text or similar
            # In our simple implementation, let's just use the text if available
            if event.metadata and "file_id" in event.metadata:
                print(f"AJA: Enriching vision for chat {chat_id}...")
                # Note: Real implementation would get file URL from Bot API
                # For now, we use the vision bridge placeholder
                content = await self.vision_bridge.describe_image(b"") # Placeholder bytes

        # 2. Add to session history
        session["history"].append({"role": "user", "text": content, "time": time.time()})
        self.gateway_state.update_session(chat_id, session)

        # 3. Chat with AgentX
        await self.telegram_adapter.send_notification(chat_id, "Thinking...", importance="low")
        response = await self.chat(content)
        
        # 4. Respond via Gateway
        await self.telegram_adapter.send_message(chat_id, response)
        
        # 5. Update session history
        session["history"].append({"role": "assistant", "text": response, "time": time.time()})
        self.gateway_state.update_session(chat_id, session)
