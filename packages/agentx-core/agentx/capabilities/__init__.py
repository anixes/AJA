from .registry import registry, CapabilityRegistry
from .base import Capability, CapabilityResult
from .terminal import TerminalExec
from .browser import BrowserNavigate, BrowserRead, BrowserSearch
from .agent_cap import AgentCapability
from agentx.agents.base import CodingAgent, BrowserAgent

# Register built-in capabilities
registry.register(TerminalExec())
registry.register(BrowserNavigate())
registry.register(BrowserRead())
registry.register(BrowserSearch())

# Register sub-agents as capabilities
registry.register(AgentCapability(CodingAgent()))
registry.register(AgentCapability(BrowserAgent()))

__all__ = ["registry", "CapabilityRegistry", "Capability", "CapabilityResult", "TerminalExec", "AgentCapability"]
