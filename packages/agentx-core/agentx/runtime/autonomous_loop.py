
import time
import asyncio
import os
import sys

# Set PYTHONPATH to current directory
sys.path.append(os.getcwd())

from agentx.goals.goal_engine import goal_engine
from agentx.autonomy.intent_engine import intent_engine

async def main_loop():
    print("[*] Starting AgentX Autonomous Loop (Phase 26)...")
    
    # 1. Start the Intent Engine (runs in a background thread)
    intent_engine.start()
    print("[*] Intent Engine started.")
    
    # Initialize Telegram Event Listener
    from agentx.interface.telegram_listener import setup_telegram_listener
    setup_telegram_listener()
    print("[*] Telegram Listener initialized.")

    while True:
        try:
            active_goals = goal_engine.get_active_goals()
            if not active_goals:
                # Part C - Self-Practice Loop
                from agentx.self_evolve.task_generator import curriculum_manager
                if curriculum_manager.should_train():
                    print("[AutonomousLoop] System idle. Generating practice task...")
                    gap = getattr(goal_engine, "_last_skill_gap", {"focus": "General practice"})
                    task = curriculum_manager.generate_training_task(gap)
                    curriculum_manager.mark_training_started()
                    
                    obj = task.get("goal", "Practice task")
                    # Part F - Safe Sandbox Only
                    goal_engine.add_goal(f"SANDBOX TRAINING: {obj}", priority=0, is_sandbox=True)
                    print(f"[AutonomousLoop] Added training task: {obj}")

            # 2. Run next step in the goal queue (Phase 24)
            # This processes goals added by the user or the Intent Engine
            goal_engine.run_step()
            
            # 3. Sleep / Cooldown
            await asyncio.sleep(5) # 5 second tick rate
            
        except KeyboardInterrupt:
            print("[!] Autonomous loop stopped by user.")
            intent_engine.stop()
            break
        except Exception as e:
            print(f"[!] Error in autonomous loop: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main_loop())
