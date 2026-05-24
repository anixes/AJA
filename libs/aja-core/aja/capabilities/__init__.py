from .registry import registry, CapabilityRegistry
from .base import Capability, CapabilityResult
from .terminal import TerminalExec
from .browser import BrowserNavigate, BrowserRead, BrowserSearch
from .agent_cap import AgentCapability
from .app_connector import AppConnectorCapability
from .handover import HandoverCapability
from .mcp_cap import MCPToolCapability
from .obsidian import ObsidianCapability
from aja.agents.base import CodingAgent, BrowserAgent

# Register built-in capabilities
registry.register(TerminalExec())
registry.register(BrowserNavigate())
registry.register(BrowserRead())
registry.register(BrowserSearch())

# Register sub-agents as capabilities
registry.register(AgentCapability(CodingAgent()))
registry.register(AgentCapability(BrowserAgent()))
registry.register(AppConnectorCapability())
registry.register(HandoverCapability())
registry.register(MCPToolCapability())
registry.register(ObsidianCapability())

__all__ = ["registry", "CapabilityRegistry", "Capability", "CapabilityResult", "TerminalExec", "AgentCapability", "MCPToolCapability", "ObsidianCapability"]
