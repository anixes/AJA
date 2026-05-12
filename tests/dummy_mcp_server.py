import asyncio
import json
import sys
from mcp.server import Server
import mcp.types as types
from mcp.server.stdio import stdio_server

# A simple MCP server that will be our 'Sub-Agent' target
server = Server("dummy-sub-agent")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="echo",
            description="Echos back the input",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"}
                },
                "required": ["text"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    if name == "echo":
        text = arguments.get("text", "no text")
        return [types.TextContent(type="text", text=f"Sub-Agent Echo: {text}")]
    raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
