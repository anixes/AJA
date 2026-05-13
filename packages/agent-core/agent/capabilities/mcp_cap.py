from .base import Capability, CapabilityResult
from agent.api.mcp_client import MCPClientManager, MCPToolCapability as MCPManager

class MCPToolCapability(Capability):
    """
    Capability to discover and use Model Context Protocol (MCP) tools.
    """
    def __init__(self):
        self.name = "mcp"
        self.description = "Interact with external apps and data via Model Context Protocol (MCP) servers."
        self.manager = MCPClientManager()
        self.wrapper = MCPManager(self.manager)

    async def execute(self, action: str, **kwargs) -> CapabilityResult:
        try:
            # Automatic connection for testing/demo if server params provided
            if "server_cmd" in kwargs:
                name = kwargs.get("server_name", "default")
                cmd = kwargs.get("server_cmd")
                args = kwargs.get("server_args", [])
                await self.manager.connect_to_server(name, cmd, args)

            result = await self.wrapper.execute(action, **kwargs)
            return CapabilityResult(success=True, output=str(result))
        except Exception as e:
            return CapabilityResult(success=False, output=f"MCP Error: {str(e)}")
