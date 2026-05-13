import asyncio
import os
import sys
from pathlib import Path

# Add packages to path
sys.path.append(str(Path(__file__).parent.parent / "packages" / "agentx-core"))

from agentx.gateway.orchestrator import UnifiedGateway
from agentx.config import TELEGRAM_TOKEN

async def main():
    token = TELEGRAM_TOKEN
    if not token:
        print("Error: TELEGRAM_TOKEN not found in .env or environment.")
        return

    gateway = UnifiedGateway()
    await gateway.initialize()
    
    print("--- AJA (Assistant of Joint Agents) Gateway ---")
    print("Status: ONLINE")
    print("Mode: PREMIUM SECRETARY")
    print("-----------------------------------------------")
    
    try:
        await gateway.run_telegram_gateway(token)
    except KeyboardInterrupt:
        print("\nAJA: Graceful shutdown initiated...")

if __name__ == "__main__":
    asyncio.run(main())
