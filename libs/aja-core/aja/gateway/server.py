import asyncio
import os
import signal
from aja.gateway.orchestrator import UnifiedGateway
from aja.memory.secretary import get_aja_memory

async def run_gateway():
    """Run the AJA Unified Gateway (single instance enforced)."""
    from aja.runtime.single_instance import acquire_lock, release_lock

    print("[*] Starting AJA Autonomous Gateway...")

    lock = acquire_lock("gateway")
    if lock is None:
        print(
            "[!] AJA Gateway is already running — refusing to start a second "
            "instance (duplicate pollers cause Telegram Conflict drops)."
        )
        return

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
        release_lock(lock)

if __name__ == "__main__":
    try:
        asyncio.run(run_gateway())
    except KeyboardInterrupt:
        pass
