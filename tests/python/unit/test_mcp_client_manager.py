import asyncio
from types import SimpleNamespace

from aja.api.mcp_client import MCPClientManager


class FakeSession:
    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="echo",
                    description="Echo input",
                    inputSchema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                )
            ]
        )

    async def call_tool(self, tool_name, arguments):
        return SimpleNamespace(content={"tool": tool_name, "arguments": arguments})


def test_mcp_manager_discovers_registry_schemas():
    async def run():
        manager = MCPClientManager()
        manager.sessions["fake"] = FakeSession()
        manager.server_configs["fake"] = SimpleNamespace(permission_scope="mcp.fake")
        tools = await manager.discover_tools("fake")
        return manager, tools

    manager, tools = asyncio.run(run())

    assert tools[0].registry_name == "mcp.fake.echo"
    schemas = manager.get_registry_schemas()
    assert schemas[0]["function"]["name"] == "mcp.fake.echo"
    assert schemas[0]["function"]["activity_type"] == "mcp"
    assert schemas[0]["function"]["required_scope"] == "mcp.fake.echo"


def test_mcp_manager_call_tool_returns_content():
    async def run():
        manager = MCPClientManager()
        manager.sessions["fake"] = FakeSession()
        return await manager.call_tool("fake", "echo", {"text": "hello"})

    result = asyncio.run(run())

    assert result == {"tool": "echo", "arguments": {"text": "hello"}}
