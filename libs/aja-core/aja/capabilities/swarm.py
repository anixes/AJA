import asyncio
from typing import Dict, Any, List
from .base import Capability

class SwarmCapability(Capability):
    """
    Capability for managing multi-agent swarms and handovers.
    """
    def __init__(self, gateway):
        super().__init__()
        self.gateway = gateway

    async def execute(self, action: str, **kwargs) -> Any:
        if action == "delegate":
            agent_id = kwargs.get("agent_id")
            task = kwargs.get("task")
            if not agent_id or not task:
                return "Error: agent_id and task are required."
            return await self.gateway.spawn_sub_agent(agent_id, task)
            
        elif action == "list_agents":
            return list(self.gateway.active_sub_agents.keys())
            
        elif action == "status":
            agent_id = kwargs.get("agent_id")
            if agent_id in self.gateway.active_sub_agents:
                return f"Agent {agent_id} is ACTIVE and connected via ACP."
            return f"Agent {agent_id} not found."
            
        return f"Unknown swarm action: {action}"

    def get_description(self) -> str:
        return "Orchestrate sub-agents and delegate complex tasks using the Baton Protocol."
