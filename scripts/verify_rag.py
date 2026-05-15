print("Starting verify_rag.py...")
try:
    from agentx.memory.secretary import get_aja_memory
    print("Import successful.")
except Exception as e:
    print(f"Import failed: {e}")
    exit(1)

def test_rag():
    print("Getting memory instance...")
    mem = get_aja_memory()
    print("Checking summary...")
    stats = mem.summary()
    print(f"Stats: {stats}")
    
    print("\nQuerying territory...")
    # Dummy vector
    vec = [0.0] * 1536
    knowledge = mem.query_territory(vec, limit=3)
    print(f"Found {len(knowledge)} chunks.")
    for k in knowledge:
        print(f"- {k['path']}")

if __name__ == "__main__":
    test_rag()
