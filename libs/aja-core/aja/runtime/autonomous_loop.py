import time
import asyncio
import os
import sys



from aja.runtime.lancedb_logger import lancedb_logger
from aja.runtime.lance_stores import LanceRuntimeStore

async def publish_heartbeats(memory, worker_id):
    """Periodically publishes the heartbeat to LanceDB in a background async loop."""
    while True:
        try:
            # Blocking disk I/O must not stall the event loop.
            await asyncio.to_thread(memory.publish_heartbeat, worker_id, "ONLINE", "AJA Worker")
        except Exception as e:
            print(f"[!] Heartbeat publish error: {e}")
        await asyncio.sleep(10)

async def _stoppable_sleep(seconds: float, stop_event) -> None:
    """Sleep for ``seconds`` unless ``stop_event`` is set first."""
    if stop_event is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def main_loop(stop_event=None):
    from aja.runtime.single_instance import acquire_lock, release_lock

    lock = acquire_lock("worker")
    if lock is None:
        print(
            "[!] Autonomous worker already running — refusing to start a "
            "duplicate instance (heartbeat churn + double mission intake)."
        )
        return

    print("[*] Starting Agent Autonomous Loop (Phase 2.0 - Hardened)...")
    heartbeat_task = None
    intent_engine_started = False
    intent_engine = None
    try:
        memory = LanceRuntimeStore()
        worker_id = "local-terminal-worker"

        # Publish initial heartbeat synchronously to mark worker ONLINE immediately
        try:
            await asyncio.to_thread(memory.publish_heartbeat, worker_id, "ONLINE", "AJA Worker")
            print("[*] Initial heartbeat published.")
        except Exception as e:
            print(f"[!] Initial heartbeat publish error: {e}")

        # Start the async background heartbeat task
        heartbeat_task = asyncio.create_task(publish_heartbeats(memory, worker_id))
        # Yield control briefly to let the task initialize in the event loop
        await asyncio.sleep(0.1)

        # 1. Start the Intent Engine (runs in a background thread)
        from aja.autonomy.intent_engine import intent_engine
        intent_engine.start()
        intent_engine_started = True
        print("[*] Intent Engine started.")

        # 2. Setup telemetry (LanceDB backed)
        # lancedb_logger initializes via singleton on import.

        # 3. Setup goal engine
        from aja.goals.goal_engine import goal_engine

        print(f"[*] AJA Autonomous Worker Started (ID: {worker_id})")

        MAX_CONSECUTIVE_ERRORS = 5
        MAX_PENDING_GOALS = 20
        BASE_SLEEP = 2
        consecutive_errors = 0

        while True:
            if stop_event is not None and stop_event.is_set():
                print("[!] Autonomous loop stopped by external stop event.")
                break
            try:
                active_goals = goal_engine.get_active_goals()

                # Backpressure: pause polling if queue is overflowing
                if len(active_goals) > MAX_PENDING_GOALS:
                    print(f"[AutonomousLoop] Backpressure threshold reached ({len(active_goals)} active goals). Pausing mission intake.")
                    await _stoppable_sleep(10, stop_event)
                    continue

                # Run next step asynchronously directly in event loop
                await goal_engine.run_step()
                consecutive_errors = 0  # Reset error counter on successful step

                # Adaptive sleep: 2s when active, 5s when idle
                sleep_time = BASE_SLEEP if active_goals else 5
                await _stoppable_sleep(sleep_time, stop_event)

            except KeyboardInterrupt:
                print("[!] Autonomous loop stopped by user.")
                break
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"[!] Circuit Breaker OPEN: {consecutive_errors} consecutive failures ({e}). Cooling down for 60s.")
                    await _stoppable_sleep(60, stop_event)
                    consecutive_errors = 0
                else:
                    backoff = min(30, BASE_SLEEP * (2 ** consecutive_errors))
                    print(f"[!] Error in autonomous loop (#{consecutive_errors}): {e}. Backing off {backoff}s.")
                    await _stoppable_sleep(backoff, stop_event)
    finally:
        # Cancellation-safe teardown: external cancel (CancelledError),
        # exceptions, and natural stop-event exits all reach this block.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            except BaseException:
                pass
        if intent_engine_started and intent_engine is not None:
            try:
                intent_engine.stop()
            except Exception as e:
                print(f"[!] Intent engine stop error: {e}")
        release_lock(lock)


if __name__ == "__main__":
    asyncio.run(main_loop())
