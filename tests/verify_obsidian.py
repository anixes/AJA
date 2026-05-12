import asyncio
import sys
import os
from pathlib import Path

# Add the packages directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "packages", "agentx-core")))

from agentx.capabilities.obsidian import ObsidianCapability

async def main():
    print("--- AgentX Phase 5 Verification: Obsidian Vault Sync ---")
    
    # 1. Setup Mock Vault
    vault_dir = Path("./tests/mock_vault")
    vault_dir.mkdir(parents=True, exist_ok=True)
    
    obsidian = ObsidianCapability(vault_path=str(vault_dir))
    
    # 2. Test Structured Note Writing (Task 5.1 & 5.3)
    print("\n[Task 5.1/5.3] Testing Structured Note Writing...")
    write_result = await obsidian.execute(
        "write_note", 
        name="Mission-001", 
        content="This is a test mission archiving task.",
        tags=["mission", "test-run"]
    )
    print(write_result.output)
    
    # Check if file has YAML frontmatter
    note_path = vault_dir / "Mission-001.md"
    if note_path.exists():
        with open(note_path, "r") as f:
            content = f.read()
            if "---" in content and "tags: [agentx, long-term-memory, mission, test-run]" in content:
                print("SUCCESS: YAML frontmatter and tags correctly applied.")
            else:
                print("FAILED: YAML frontmatter missing or incorrect.")
    
    # 3. Test Appending (Task 5.2)
    print("\n[Task 5.2] Testing Note Appending...")
    append_result = await obsidian.execute(
        "append_to_note",
        name="Mission-001",
        content="Sub-agent 'researcher_01' has started work."
    )
    print(append_result.output)
    
    with open(note_path, "r") as f:
        content = f.read()
        if "Sub-agent 'researcher_01'" in content:
            print("SUCCESS: Log entry appended successfully.")
        else:
            print("FAILED: Append logic failed.")

    # 4. Clean up (Optional, but good for tests)
    # os.remove(note_path)
    # os.rmdir(vault_dir)

    print("\n--- Verification Complete ---")

if __name__ == "__main__":
    asyncio.run(main())
