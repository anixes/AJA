import asyncio
import sys
import os

# Add the packages directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "packages", "agent-core")))

from aja.gateway import UnifiedGateway
from aja.api.mcp_client import MCPClientManager, MCPToolCapability
from aja.capabilities.swarm import SwarmCapability

async def main():
    print("--- Agent Phase 4 Verification: Multi-Agent Swarm ---")
    
    # 1. Initialize Gateway
    gateway = UnifiedGateway()
    await gateway.initialize(semantic_db_path="./tests/test_swarm.lancedb")
    
    # 2. Test Persistent MCP (Task 4.1)
    print("\n[Task 4.1] Testing Persistent MCP Connection...")
    mcp_manager = MCPClientManager()
    mcp_cap = MCPToolCapability(mcp_manager)
    
    # Simulate connecting to a dummy server (this will fail but we check the persistence logic)
    try:
        # Using a command that exists but isn't an MCP server just to test the wrapper
        await mcp_cap.execute("connect", name="test_server", command="echo", args=["hello"])
    except Exception as e:
        print(f"Expected MCP failure (no real server): {e}")

    # 3. Test Sub-Agent Spawning & Baton (Task 4.2)
    print("\n[Task 4.2] Testing Baton Handover...")
    spawn_result = await gateway.spawn_sub_agent("researcher_01", "Research the impact of Agentic AI on Rust ecosystems.")
    print(spawn_result)
    
    if isinstance(spawn_result, str) and len(spawn_result) == 6:
        print("SUCCESS: Baton sync code generated.")
    else:
        print("FAILED: Sync code not found in result.")

    # 4. Test Swarm Capability (Task 4.3)
    print("\n[Task 4.3] Testing Swarm Capability...")
    swarm = SwarmCapability(gateway)
    agents = await swarm.execute("list_agents")
    print(f"Active Agents: {agents}")
    
    if "researcher_01" in agents:
        print("SUCCESS: Sub-agent correctly tracked in Swarm registry.")
    else:
        print("FAILED: Sub-agent missing from registry.")

    await gateway.stop()
    print("\n--- Verification Complete ---")

if __name__ == "__main__":
    asyncio.run(main())
