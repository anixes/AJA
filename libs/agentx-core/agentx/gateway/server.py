import asyncio
import os
import signal
from agentx.gateway.orchestrator import UnifiedGateway
from agentx.memory.secretary import get_aja_memory

async def run_gateway():
    """Run the AJA Unified Gateway."""
    print("[*] Starting AJA Autonomous Gateway...")
    
    # Initialize the Gateway
    gateway = UnifiedGateway()
    
    # Start the Gateway (this starts Telegram polling and Telemetry)
    await gateway.start()
    
    print("[*] AJA Gateway is active. Listening for missions...")
    
    # Keep alive and handle signals
    stop_event = asyncio.Event()
    
    def handle_exit():
        print("\n[*] Shutting down AJA Gateway...")
        stop_event.set()

    # Loop for health checks or just wait
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        handle_exit()
    finally:
        await gateway.stop()

if __name__ == "__main__":
    try:
        asyncio.run(run_gateway())
    except KeyboardInterrupt:
        pass
