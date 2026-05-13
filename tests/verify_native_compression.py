import json
import asyncio
import sys
import os

# Add the packages directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "packages", "agent-core")))

import agent_native

async def main():
    print("--- Agent: Native Adaptive Context Verification ---")
    
    # 1. Initialize the Native Trajectory Manager
    # We specify the model so it can use the correct BPE (tiktoken)
    tm = agent_native.PyTrajectoryManager("gpt-4o")
    
    # 2. Create a "Heavy" History
    # We'll simulate a conversation with a large middle block
    messages = [
        {"role": "system", "content": "You are Agent, a master orchestrator."},
        {"role": "human", "content": "Analyze the codebase and provide a summary."},
        {"role": "gpt", "content": "I am starting the analysis..."},
        {"role": "tool", "content": "Executing 'ls -R'..." + "A" * 5000}, # Large tool output
        {"role": "gpt", "content": "I found several modules..."},
        {"role": "tool", "content": "Reading 'lib.rs'..." + "B" * 5000}, # Another large output
        {"role": "human", "content": "Actually, focus on the compression logic."},
    ]
    
    messages_json = json.dumps(messages)
    
    # 3. Analyze for Compression
    # Limit: 2000 tokens
    # Head: 2 (Protect system + first human)
    # Tail: 1 (Protect the latest request)
    print("\nAnalyzing trajectory for compression...")
    analysis_raw = tm.analyze(messages_json, limit=2000, head=2, tail=1)
    analysis = json.loads(analysis_raw)
    
    print(f"Total Tokens: {analysis['total_tokens']}")
    print(f"Should Compress: {analysis['should_compress']}")
    
    if analysis['should_compress']:
        start = analysis['compress_start']
        end = analysis['compress_end']
        print(f"Compression Region Detected: Turn {start} to Turn {end}")
        
        # Verify the slicing
        compressible_turns = messages[start:end]
        print(f"Compressing {len(compressible_turns)} middle turns...")
        
        # In a real run, Agent would now call its Summarizer capability
        # for these specific turns.
        print("\nSUCCESS: Native engine correctly identified the 'Agent Strategic Middle' for summarization.")
    else:
        print("\nFAILED: Trajectory was not flagged for compression.")

if __name__ == "__main__":
    asyncio.run(main())
