import asyncio
import sys
import os
import json

# Add packages to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "packages", "agent-core")))

from agent.api.mcp_client import MCPClientManager

async def test_mcp():
    print("--- Agent: Native MCP Tool Discovery Verification ---")
    manager = MCPClientManager()
    
    # Path to the dummy server
    server_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "dummy_mcp_server.py"))
    python_exe = sys.executable
    
    print(f"Connecting to Dummy Sub-Agent at {server_script}...")
    await manager.connect_to_server(
        "dummy", 
        python_exe, 
        [server_script]
    )
    
    # List tools
    tools = await manager.list_tools("dummy")
    print(f"Discovered Tools: {[t.name for t in tools]}")
    
    # Call tool
    print("Calling 'echo' tool...")
    result = await manager.call_tool("dummy", "echo", {"text": "Hello Swarm!"})
    print(f"Result: {result[0].text}")
    
    await manager.disconnect_all()
    print("\nSUCCESS: Agent successfully discovered and controlled a sub-agent tool.")

if __name__ == "__main__":
    asyncio.run(test_mcp())
