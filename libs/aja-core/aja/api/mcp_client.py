from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MCPToolDefinition:
    server_id: str
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    permission_scope: str = ""

    @property
    def registry_name(self) -> str:
        return f"mcp.{self.server_id}.{self.name}"


class MCPClientManager:
    """
    Persistent multi-server MCP manager.
    Supports stdio through the official mcp package and SSE when available.
    """

    def __init__(self):
        self.sessions: Dict[str, Any] = {}
        self.stacks: Dict[str, AsyncExitStack] = {}
        self.server_configs: Dict[str, Any] = {}
        self.tools: Dict[str, MCPToolDefinition] = {}

    async def boot_from_config(self, config: Optional[Any] = None) -> None:
        if config is None:
            from aja.config import CONFIG
            servers = getattr(CONFIG, "mcp_servers", [])
        else:
            servers = getattr(config, "mcp_servers", config)

        for server in servers or []:
            if getattr(server, "enabled", True):
                await self.connect_config(server)

    async def connect_config(self, server: Any) -> None:
        server_id = getattr(server, "server_id")
        transport = getattr(server, "transport", "stdio")
        self.server_configs[server_id] = server
        if transport == "stdio":
            await self.connect_stdio(server_id, getattr(server, "command", None), list(getattr(server, "args", []) or []))
        elif transport == "sse":
            await self.connect_sse(server_id, getattr(server, "url", None))
        else:
            raise ValueError(f"Unsupported MCP transport: {transport}")
        await self.discover_tools(server_id)

    async def connect_to_server(self, name: str, command: str, args: List[str]):
        await self.connect_stdio(name, command, args)
        await self.discover_tools(name)

    async def connect_stdio(self, server_id: str, command: Optional[str], args: List[str]) -> None:
        if server_id in self.sessions:
            return
        if not command:
            raise ValueError(f"MCP server '{server_id}' is missing a stdio command.")

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_params = StdioServerParameters(command=command, args=args, env=None)
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(server_params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions[server_id] = session
            self.stacks[server_id] = stack
        except Exception:
            await stack.aclose()
            raise

    async def connect_sse(self, server_id: str, url: Optional[str]) -> None:
        if server_id in self.sessions:
            return
        if not url:
            raise ValueError(f"MCP server '{server_id}' is missing an SSE URL.")

        from mcp import ClientSession
        try:
            from mcp.client.sse import sse_client
        except ImportError as exc:
            raise RuntimeError("The installed mcp package does not expose SSE client support.") from exc

        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(sse_client(url))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions[server_id] = session
            self.stacks[server_id] = stack
        except Exception:
            await stack.aclose()
            raise

    async def disconnect(self, server_id: str) -> None:
        stack = self.stacks.pop(server_id, None)
        if stack:
            await stack.aclose()
        self.sessions.pop(server_id, None)
        for key in [key for key, value in self.tools.items() if value.server_id == server_id]:
            self.tools.pop(key, None)

    async def disconnect_all(self):
        for server_id in list(self.stacks):
            await self.disconnect(server_id)

    async def reload(self, server_id: str) -> List[MCPToolDefinition]:
        config = self.server_configs.get(server_id)
        if config is None:
            raise ValueError(f"No MCP config known for server '{server_id}'.")
        await self.disconnect(server_id)
        await self.connect_config(config)
        return [tool for tool in self.tools.values() if tool.server_id == server_id]

    async def discover_tools(self, server_id: str) -> List[MCPToolDefinition]:
        raw_tools = await self.list_tools(server_id)
        server_config = self.server_configs.get(server_id)
        base_scope = getattr(server_config, "permission_scope", None) or f"mcp.{server_id}"
        discovered = []
        for raw in raw_tools:
            name = getattr(raw, "name", None) or (raw.get("name") if isinstance(raw, dict) else None)
            if not name:
                continue
            description = getattr(raw, "description", "") or (raw.get("description", "") if isinstance(raw, dict) else "")
            input_schema = (
                getattr(raw, "inputSchema", None)
                or getattr(raw, "input_schema", None)
                or (raw.get("inputSchema", {}) if isinstance(raw, dict) else {})
            )
            tool = MCPToolDefinition(
                server_id=server_id,
                name=name,
                description=description,
                input_schema=input_schema or {"type": "object", "properties": {}},
                permission_scope=f"{base_scope}.{name}",
            )
            self.tools[tool.registry_name] = tool
            discovered.append(tool)
        return discovered

    async def list_tools(self, server_name: str) -> List[Any]:
        if server_name not in self.sessions:
            return []
        tools = await self.sessions[server_name].list_tools()
        return getattr(tools, "tools", tools)

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if server_name not in self.sessions:
            raise ValueError(f"MCP server '{server_name}' is not connected.")
        result = await self.sessions[server_name].call_tool(tool_name, arguments)
        return getattr(result, "content", result)

    def get_registry_schemas(self) -> List[Dict[str, Any]]:
        schemas = []
        for tool in self.tools.values():
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.registry_name,
                    "activity_type": "mcp",
                    "retry_policy": "safe",
                    "required_scope": tool.permission_scope,
                    "description": tool.description or f"MCP tool {tool.name} from {tool.server_id}",
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                    "metadata": {"server_id": tool.server_id, "mcp_tool": tool.name},
                },
            })
        return schemas


class MCPToolCapability:
    def __init__(self, manager: MCPClientManager):
        self.manager = manager

    async def execute(self, action: str, **kwargs):
        if action == "connect":
            await self.manager.connect_to_server(kwargs.get("name"), kwargs.get("command"), kwargs.get("args", []))
            return f"Connected to {kwargs.get('name')}"
        if action == "list_servers":
            return list(self.manager.sessions.keys())
        if action == "list_tools":
            return await self.manager.list_tools(kwargs.get("server"))
        if action == "call_tool":
            return await self.manager.call_tool(kwargs.get("server"), kwargs.get("tool"), kwargs.get("args", {}))
        if action == "reload":
            return await self.manager.reload(kwargs.get("server"))
        return f"Unknown action: {action}"


_DEFAULT_MCP_MANAGER: Optional[MCPClientManager] = None


def get_default_mcp_manager() -> MCPClientManager:
    global _DEFAULT_MCP_MANAGER
    if _DEFAULT_MCP_MANAGER is None:
        _DEFAULT_MCP_MANAGER = MCPClientManager()
    return _DEFAULT_MCP_MANAGER
