import asyncio
import os
import logging
from agentx.orchestration.swarm import SwarmEngine

logging.basicConfig(level=logging.INFO)

async def main():
    # Configure environment
    os.environ["AI_PROVIDER"] = "google"
    os.environ["GEMINI_API_KEY"] = "AIzaSyD5i1WDARmil2BAa9XAkCgW5uzztpohCcg"
    model = "gemini-1.5-flash"
    
    print(f"Initializing SwarmEngine with model {model}...")
    engine = SwarmEngine(model=model)
    
    objective = "Analyze the AJAMemory implementation in secretary.py and verify if the new territory_knowledge table is correctly being used by the TerritoryScanner."
    
    print(f"\n🚀 STARTING OVERDRIVE TEST MISSION: {objective}\n")
    try:
        await engine.plan_and_execute_batons(objective)
    except Exception as e:
        print(f"Mission failed: {e}")

if __name__ == "__main__":
    print("Script started.")
    asyncio.run(main())
