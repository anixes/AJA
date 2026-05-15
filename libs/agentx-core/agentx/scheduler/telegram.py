import json
from agentx.scheduler.scheduler import scheduler
from agentx.autonomy.intent_engine import intent_engine
from agentx.goals.goal_engine import goal_engine
from agentx.config import TELEGRAM_TOKEN, TELEGRAM_ALLOWED_USER_ID
from agentx.interface.intent_parser import parse_intent
from agentx.interface.telegram_listener import async_send_telegram_message

async def handle_telegram_message(text: str, user_id: str, session):
    """Conversational Router for Telegram messages."""
    
    # Security check
    if TELEGRAM_ALLOWED_USER_ID and str(user_id) != str(TELEGRAM_ALLOWED_USER_ID):
        await async_send_telegram_message(user_id, "Unauthorized user.")
        return

    # Build system state for contextual awareness
    active_goals = [{"objective": g.objective, "priority": g.priority, "status": g.status} 
                    for g in goal_engine.goals if g.status in ["PENDING", "RUNNING"]]
    system_state = {
        "autonomy_enabled": goal_engine.autonomy_enabled,
        "is_interrupted": goal_engine.is_interrupted,
        "active_goals": active_goals
    }

    # Run intent parsing with session history and system state
    intent_data = parse_intent(text, session.history, system_state)
    
    intent_type = intent_data.get("type", "question")
    response_text = intent_data.get("response", "I'm not sure how to handle that.")
    
    # Pre-send the conversational response (e.g., "Alright, starting that now.")
    if response_text:
        session.log_interaction("assistant", response_text)
        await async_send_telegram_message(user_id, response_text)

    # Act on the intent
    if intent_type == "goal" and intent_data.get("goal"):
        goal_text = intent_data.get("goal")
        goal_engine.add_goal(goal_text)
        # We don't send "Goal added" because the response_text handles it naturally
        
    elif intent_type == "control" and intent_data.get("command"):
        cmd = intent_data.get("command").lower()
        if cmd == "pause":
            # pause active goal if possible or interrupt global
            goal_engine.is_interrupted = True
        elif cmd == "resume":
            goal_engine.is_interrupted = False
        elif cmd == "auto_on":
            intent_engine.autonomy_enabled = True
        elif cmd == "auto_off":
            intent_engine.autonomy_enabled = False
        # The conversational response_text covers the confirmation.

def _send_telegram_report(message: str):
    """Legacy sync wrapper for IntentEngine usage. Use async_send_telegram_message instead where possible."""
    import asyncio
    
    if not TELEGRAM_TOKEN or not TELEGRAM_ALLOWED_USER_ID:
        print(f"[Telegram Bot] [MOCKED] REPORT: {message}")
        return

    def get_loop():
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    loop = get_loop()
    if loop and loop.is_running():
        asyncio.create_task(async_send_telegram_message(TELEGRAM_ALLOWED_USER_ID, message))
    else:
        try:
            asyncio.run(async_send_telegram_message(TELEGRAM_ALLOWED_USER_ID, message))
        except Exception as e:
            print(f"[Telegram Sync Fallback] Error: {e}")

