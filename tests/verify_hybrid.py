import asyncio
import json
import os
import shutil
from aja.gateway import UnifiedGateway

async def verify():
    print("--- Agent Hybrid Verification ---")
    
    # 1. Initialize Gateway
    gateway = UnifiedGateway()
    db_path = "./tests/test_memory.lancedb"
    if os.path.exists(db_path):
        shutil.rmtree(db_path)
    
    print(f"Initializing Native Core with DB at {db_path}...")
    await gateway.initialize(semantic_db_path=db_path)
    
    # 2. Test Chat (which triggers native memory and translation)
    print("\nTesting Unified Chat Loop...")
    user_msg = "Hello Agent! How are you today?"
    response = await gateway.chat(user_msg)
    print(f"User: {user_msg}")
    print(f"Response: {response}")
    
    # 3. Verify Native Translation directly
    print("\nTesting Native Translation Layer...")
    import aja_native
    sample_request = {
        "model": "claude-3-5-sonnet",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "text", "text": "What is the capital of France?"}]
            }
        ]
    }
    translated = aja_native.translate_to_anthropic(json.dumps(sample_request))
    translated_data = json.loads(translated)
    print(f"Translated Model: {translated_data.get('model')}")
    print(f"Message Count: {len(translated_data.get('messages', []))}")
    
    # 4. Verify Semantic Memory storage
    print("\nVerifying Semantic Memory file creation...")
    if os.path.exists(db_path):
        print(f"SUCCESS: Semantic DB created at {db_path}")
    else:
        print("FAILURE: Semantic DB not found!")

    print("\n--- Verification Complete ---")

if __name__ == "__main__":
    asyncio.run(verify())
