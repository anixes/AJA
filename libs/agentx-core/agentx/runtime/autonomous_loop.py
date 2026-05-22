import time
import asyncio
import os
import sys

# Set PYTHONPATH to current directory
sys.path.append(os.getcwd())

from agentx.runtime.lancedb_logger import lancedb_logger
from agentx.memory.secretary import AJAMemory

async def publish_heartbeats(memory, worker_id):
    """Periodically publishes the heartbeat to LanceDB in a background async loop."""
    while True:
        try:
            memory.publish_heartbeat(worker_id, name="AJA Worker")
        except Exception as e:
            print(f"[!] Heartbeat publish error: {e}")
        await asyncio.sleep(10)

async def main_loop():
    print("[*] Starting Agent Autonomous Loop (Phase 2.0 - Hardened)...")
    memory = AJAMemory()
    worker_id = "local-terminal-worker"
    
    # Publish initial heartbeat synchronously to mark worker ONLINE immediately
    try:
        memory.publish_heartbeat(worker_id, name="AJA Worker")
        print("[*] Initial heartbeat published.")
    except Exception as e:
        print(f"[!] Initial heartbeat publish error: {e}")
        
    # Start the async background heartbeat task
    heartbeat_task = asyncio.create_task(publish_heartbeats(memory, worker_id))
    # Yield control briefly to let the task initialize in the event loop
    await asyncio.sleep(0.1)
    
    # 1. Start the Intent Engine (runs in a background thread)
    from agentx.autonomy.intent_engine import intent_engine
    intent_engine.start()
    print("[*] Intent Engine started.")
    
    # 2. Setup telemetry (LanceDB backed)
    # lancedb_logger initializes via singleton on import.
    
    # 3. Setup goal engine
    from agentx.goals.goal_engine import goal_engine
    
    print(f"[*] AJA Autonomous Worker Started (ID: {worker_id})")
    
    while True:
        try:
            active_goals = goal_engine.get_active_goals()
            # if not active_goals:
            #     # Part C - Self-Practice Loop
            #     from agentx.self_evolve.task_generator import curriculum_manager
            #     if curriculum_manager.should_train():
            #         print("[AutonomousLoop] System idle. Generating practice task...")
            #         gap = getattr(goal_engine, "_last_skill_gap", {"focus": "General practice"})
            #         task = curriculum_manager.generate_training_task(gap)
            #         curriculum_manager.mark_training_started()
            #         
            #         obj = task.get("goal", "Practice task")
            #         # Part F - Safe Sandbox Only
            #         goal_engine.add_goal(f"SANDBOX TRAINING: {obj}", priority=0, is_sandbox=True)
            #         print(f"[AutonomousLoop] Added training task: {obj}")
 
            # 4. Run next step in the goal queue in a separate worker thread
            await asyncio.to_thread(goal_engine.run_step)
            
            # 5. Sleep / Cooldown
            await asyncio.sleep(2) # 2 second tick rate
            
        except KeyboardInterrupt:
            print("[!] Autonomous loop stopped by user.")
            intent_engine.stop()
            heartbeat_task.cancel()
            break
        except Exception as e:
            print(f"[!] Error in autonomous loop: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main_loop())
