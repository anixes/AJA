import os
import requests
import json
import hashlib
from datetime import datetime, timezone, timedelta

# Config
NOTIFICATIONS_ENABLED = os.environ.get("AGENTX_NOTIFICATIONS_ENABLED", "True").lower() in ("true", "1", "yes")
MAX_NOTIFICATIONS_PER_MINUTE = 10
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")

# Rate Limiting State
_notification_timestamps = []
_last_alert_hashes = {}  # { event_type: (hash, timestamp) }

def _can_send(event_type: str, message: str) -> bool:
    if not NOTIFICATIONS_ENABLED:
        return False
        
    now = datetime.now(timezone.utc)
    
    # Check rate limit
    global _notification_timestamps
    _notification_timestamps = [t for t in _notification_timestamps if now - t < timedelta(minutes=1)]
    if len(_notification_timestamps) >= MAX_NOTIFICATIONS_PER_MINUTE:
        return False
        
    # Collapse duplicates (same type + same hash within 5 minutes)
    msg_hash = hashlib.md5(message.encode()).hexdigest()
    if event_type in _last_alert_hashes:
        last_hash, last_time = _last_alert_hashes[event_type]
        if last_hash == msg_hash and (now - last_time) < timedelta(minutes=5):
            return False
            
    _last_alert_hashes[event_type] = (msg_hash, now)
    _notification_timestamps.append(now)
    return True

def _send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"🤖 *AgentX Alert*\n\n{message}",
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[Notifier] Telegram send failed: {e}")

def send_notification(event_type: str, payload: dict):
    """
    Supported event types:
    - TASK_COMPLETED
    - TASK_FAILED
    - TASK_STALLED
    - CIRCUIT_BREAKER_TRIGGERED
    - HIGH_RISK_TASK_STARTED
    """
    message = ""
    
    if event_type == "TASK_COMPLETED":
        task_id = payload.get("task_id", "?")
        obj = payload.get("objective", "Unknown")
        message = f"✅ *Task Completed*\nID: {task_id}\n`{obj}`"
        
    elif event_type == "TASK_FAILED":
        task_id = payload.get("task_id", "?")
        obj = payload.get("objective", "Unknown")
        err = payload.get("error", "Unknown error")
        message = f"❌ *Task Failed*\nID: {task_id}\n`{obj}`\n\nError: _{err}_"
        
    elif event_type == "TASK_STALLED":
        task_id = payload.get("task_id", "?")
        message = f"🛑 *Task Stalled*\nTask {task_id} has made no progress after multiple retries and has been marked as permanently failed."
        
    elif event_type == "CIRCUIT_BREAKER_TRIGGERED":
        reason = payload.get("reason", "Unknown")
        message = f"🚨 *CIRCUIT BREAKER TRIGGERED*\nAgent Loop has been paused.\nReason: _{reason}_"
        
    elif event_type == "HIGH_RISK_TASK_STARTED":
        task_id = payload.get("task_id", "?")
        obj = payload.get("objective", "Unknown")
        message = f"⚠️ *High Risk Task Started*\nID: {task_id}\n`{obj}`"
        
    elif event_type == "TRIGGER_FIRED":
        trigger_id = payload.get("trigger_id", "?")
        task_id = payload.get("task_id", "?")
        message = f"⚡ *Trigger Fired*\nTrigger: {trigger_id}\nTask created: {task_id}"
        
    else:
        # Ignore unknown types for notifications
        return
        
    if _can_send(event_type, message):
        _send_telegram(message)
