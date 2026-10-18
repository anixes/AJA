"""
=============================================================================
AJA Model Context Protocol (MCP) Universal Dynamic Mesh Unit Tests
=============================================================================
"""

import pytest
from aja.mcp.mcp_client_manager import MCPClientManager, MCPToolDefinition


def test_mcp_direct_tools_registration_and_listing():
    """Verify in-process MCP tool registration, listing, and metadata schema retrieval."""
    manager = MCPClientManager(default_token_budget=2048)

    tools_spec = [
        {
            "name": "query_database",
            "description": "Executes read-only SQL query on PostgreSQL replica",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "maxTokenBudget": 1024,
        },
        {
            "name": "fetch_logs",
            "description": "Fetches recent container logs from journald",
            "inputSchema": {
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        },
    ]

    manager.register_direct_tools("postgres_mcp", tools_spec)
    
    discovered = manager.list_tools()
    assert len(discovered) == 2

    tool = manager.get_tool("postgres_mcp", "query_database")
    assert tool is not None
    assert tool.max_token_budget == 1024
    assert tool.input_schema["required"] == ["query"]


@pytest.mark.anyio
async def test_mcp_stateless_tool_call():
    """Verify stateless tool invocation with token budgeting."""
    manager = MCPClientManager()
    manager.register_direct_tools(
        "filesystem_mcp",
        [{"name": "read_file", "description": "Reads file contents", "maxTokenBudget": 512}],
    )

    result = await manager.call_tool(
        server_name="filesystem_mcp",
        tool_name="read_file",
        arguments={"path": "/etc/hosts"},
    )
    assert result["success"] is True
    assert result["token_budget"] == 512
    assert "filesystem_mcp:read_file" in result["tool"]
