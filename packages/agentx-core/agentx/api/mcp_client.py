import asyncio
import json
import subprocess
from typing import List, Dict, Any, Optional, Tuple
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPClientManager:
    """
    Manages persistent connections to multiple MCP servers for AgentX.
    """
    def __init__(self):
        self.sessions: Dict[str, ClientSession] = {}
        self.stacks: Dict[str, AsyncExitStack] = {}

    async def connect_to_server(self, name: str, command: str, args: List[str]):
        """Connect to a stdio-based MCP server and keep it alive."""
        if name in self.sessions:
            return

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=None
        )
        
        stack = AsyncExitStack()
        try:
            # Enter the stdio client context
            read, write = await stack.enter_async_context(stdio_client(server_params))
            
            # Enter the session context
            session = await stack.enter_async_context(ClientSession(read, write))
            
            # Initialize the session
            await session.initialize()
            
            self.sessions[name] = session
            self.stacks[name] = stack
            print(f"AgentX: Persistent connection established to MCP server '{name}'")
            
        except Exception as e:
            await stack.aclose()
            print(f"AgentX Error: Failed to connect to MCP server '{name}': {e}")
            raise

    async def disconnect_all(self):
        """Cleanly close all MCP connections."""
        for name, stack in self.stacks.items():
            await stack.aclose()
        self.sessions.clear()
        self.stacks.clear()

    async def list_tools(self, server_name: str) -> List[Any]:
        if server_name not in self.sessions:
            return []
        session = self.sessions[server_name]
        tools = await session.list_tools()
        return tools.tools

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if server_name not in self.sessions:
            raise ValueError(f"Server '{server_name}' not found. Please connect first.")
        session = self.sessions[server_name]
        result = await session.call_tool(tool_name, arguments)
        return result.content

class MCPToolCapability:
    """
    AgentX Capability to interact with MCP servers.
    """
    def __init__(self, manager: MCPClientManager):
        self.manager = manager

    async def execute(self, action: str, **kwargs):
        if action == "connect":
            name = kwargs.get("name")
            cmd = kwargs.get("command")
            args = kwargs.get("args", [])
            await self.manager.connect_to_server(name, cmd, args)
            return f"Connected to {name}"
            
        elif action == "list_servers":
            return list(self.manager.sessions.keys())
            
        elif action == "list_tools":
            server = kwargs.get("server")
            return await self.manager.list_tools(server)
            
        elif action == "call_tool":
            server = kwargs.get("server")
            tool = kwargs.get("tool")
            args = kwargs.get("args", {})
            return await self.manager.call_tool(server, tool, args)
            
        return f"Unknown action: {action}"
