import logging
from aja.autonomy.intent_engine import intent_engine
from aja.goals.goal_engine import goal_engine
from aja.config import TELEGRAM_ALLOWED_USER_ID
from aja.interface.intent_parser import parse_intent
from aja.interface.telegram_listener import async_send_telegram_message
from aja.runtime.events import LanceRuntimeEventSink

logger = logging.getLogger(__name__)

async def handle_telegram_message(text: str, user_id: str, session):
    """Legacy conversational router kept for compatibility with webhook paths."""
    
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
    """Legacy shim: emits runtime events for UnifiedGateway telemetry tailing."""
    logger.warning(
        "legacy_telegram_report_path_used",
        extra={"message_length": len(message or "")},
    )
    try:
        LanceRuntimeEventSink().emit(
            {
                "event_type": "LEGACY_TELEGRAM_REPORT",
                "tool": "intent_engine",
                "message": message or "",
                "level": "info",
                "metadata": {"source": "scheduler.telegram._send_telegram_report"},
            }
        )
    except Exception:
        logger.exception("Failed to emit legacy telegram runtime event")
        logger.info("legacy_telegram_report_dropped", extra={"message": message or ""})
