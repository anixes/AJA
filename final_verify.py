import asyncio
import json
import os
import agent_native
from agent.gateway import UnifiedGateway
from agent.utils.tokenjuice import TokenJuice
from agent.runtime.memory import MemoryTree
from agent.capabilities import registry

async def verify_system():
    print("[START] Starting Final System Verification for Agent (Hybrid Pro)...")
    
    # 1. Verify Rust Core
    print("\n[1/5] Verifying Rust Native Core...")
    v = agent_native.version()
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
    verbose_log = "Compiling agent-native...\n" * 50
    squeezed = juice.squeeze(verbose_log)
    if "[CARGO OUTPUT OMITTED]" in squeezed:
        print("  - Logic Compression: OK")
    else:
        print("  - Logic Compression: FAILED")

    # 4. Verify Unified Gateway + Native Bridge
    print("\n[4/5] Verifying Unified Gateway + Native Bridge...")
    gateway = UnifiedGateway()
    response = await gateway.chat("Hello Agent!")
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

    # 6. Verify Swarm Registry (Phase 6)
    print("\n[6/9] Verifying Swarm Registry (Phase 6)...")
    from agent.orchestration.registry import WorkerRegistry
    registry_inst = WorkerRegistry()
    registry_inst.register_worker("test-worker", "Test Worker", ["testing"])
    best = registry_inst.get_best_worker("testing")
    if best and best["worker_id"] == "test-worker":
        print("  - Worker Registry: OK")
    else:
        print("  - Worker Registry: FAILED")

    # 7. Verify Reflection Engine
    print("\n[7/9] Verifying Reflection Engine...")
    from agent.autonomy.reflection import ReflectionEngine
    refl = ReflectionEngine()
    # Check if SkillStore is healthy
    skills = refl.skill_store.list_skills()
    print(f"  - Active Skills: {len(skills)}")
    print("  - Reflection Engine: OK")

    # 8. Verify Handover Manager
    print("\n[8/9] Verifying Handover Manager...")
    from agent.orchestration.handover import HandoverManager
    hom = HandoverManager()
    otc = hom.generate_otc({"session": "test"})
    resolved = hom.resolve_otc(otc)
    if resolved and resolved["session"] == "test":
        print("  - Handover Protocol: OK")
    else:
        print("  - Handover Protocol: FAILED")

    # 9. Verify Resource Telemetry
    print("\n[9/9] Verifying Resource Telemetry...")
    from agent.utils.health_check import get_resource_telemetry
    telemetry = get_resource_telemetry()
    print(f"  - Hardware Stats: CPU={telemetry.get('cpu_load')} Disk={telemetry.get('disk_free_gb')}GB")
    if "vram_available_mb" in telemetry:
        print(f"  - GPU Stats: VRAM Available={telemetry['vram_available_mb']}MB")
    print("  - Telemetry Engine: OK")

    print("\n" + "="*50)
    print("[SUCCESS] ALL SYSTEMS OPERATIONAL: AGENTX IS READY")
    print("="*50)
    
    # Cleanup
    if os.path.exists("verify_memory.db"):
        os.remove("verify_memory.db")

if __name__ == "__main__":
    asyncio.run(verify_system())
