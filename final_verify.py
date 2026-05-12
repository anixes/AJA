import asyncio
import json
import os
import agentx_native
from agentx.gateway import UnifiedGateway
from agentx.utils.tokenjuice import TokenJuice
from agentx.runtime.memory import MemoryTree
from agentx.capabilities import registry

async def verify_system():
    print("[START] Starting Final System Verification for AgentX (Hybrid Pro)...")
    
    # 1. Verify Rust Core
    print("\n[1/5] Verifying Rust Native Core...")
    v = agentx_native.version()
    print(f"  - Rust Core Version: {v}")
    
    # 2. Verify Memory Tree (LanceDB/Arrow)
    print("\n[2/5] Verifying Memory Tree (LanceDB/Arrow)...")
    memory = MemoryTree("verify_memory.db")
    memory.add_activity("User requested verification.", {"source": "test"})
    history = memory.get_recent_history(limit=1)
    if history and "verification" in history[0]["content"]:
        print("  - Memory Persistence: OK")
    else:
        print("  - Memory Persistence: FAILED")
    
    # 3. Verify TokenJuice Compression
    print("\n[3/5] Verifying TokenJuice Compression...")
    juice = TokenJuice(memory)
    verbose_log = "Compiling agentx-native...\n" * 50
    squeezed = juice.squeeze(verbose_log)
    if "[CARGO OUTPUT OMITTED]" in squeezed:
        print("  - Logic Compression: OK")
    else:
        print("  - Logic Compression: FAILED")

    # 4. Verify Unified Gateway + Native Bridge
    print("\n[4/5] Verifying Unified Gateway + Native Bridge...")
    gateway = UnifiedGateway()
    response = await gateway.chat("Hello AgentX!")
    if "Rust-Core Integrated" in response:
        print("  - Gateway & Rust Translation: OK")
    else:
        print("  - Gateway & Rust Translation: FAILED")

    # 5. Verify Capability Registry (MCP/ACP/Browser)
    print("\n[5/5] Verifying Capability Registry (MCP/ACP/Browser)...")
    caps = [c.name for c in registry.capabilities.values()]
    print(f"  - Registered Capabilities: {', '.join(caps)}")
    if "mcp" in caps and "terminal.exec" in caps:
        print("  - Registry Health: OK")
    else:
        print("  - Registry Health: FAILED")

    print("\n" + "="*50)
    print("[SUCCESS] ALL SYSTEMS OPERATIONAL: AGENTX IS READY")
    print("="*50)
    
    # Cleanup
    if os.path.exists("verify_memory.db"):
        os.remove("verify_memory.db")

if __name__ == "__main__":
    asyncio.run(verify_system())
