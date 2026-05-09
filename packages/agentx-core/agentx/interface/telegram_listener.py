import json
import urllib.request
import asyncio
from agentx.runtime.event_bus import bus, EVENTS
from agentx.config import TELEGRAM_TOKEN, TELEGRAM_ALLOWED_USER_ID

async def async_send_telegram_message(chat_id: str, message: str):
    """Non-blocking Telegram send."""
    if not TELEGRAM_TOKEN or not TELEGRAM_ALLOWED_USER_ID:
        print(f"[Telegram Bot] [MOCKED] {message}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    
    def _send():
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                pass
        except Exception as e:
            print(f"[Telegram] Send failed: {e}")
            
    # Run the blocking request in a separate thread so we don't block the async loop
    await asyncio.to_thread(_send)

def setup_telegram_listener():
    """Subscribe to EventBus and forward events to Telegram."""
    
    def get_loop():
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def broadcast(msg: str):
        if not TELEGRAM_ALLOWED_USER_ID:
            print(f"[Telegram Listener] MOCKED: {msg}")
            return
            
        loop = get_loop()
        if loop and loop.is_running():
            asyncio.create_task(async_send_telegram_message(TELEGRAM_ALLOWED_USER_ID, msg))
        else:
            # Fallback for sync contexts (though autonomous loop uses async)
            try:
                asyncio.run(async_send_telegram_message(TELEGRAM_ALLOWED_USER_ID, msg))
            except Exception as e:
                print(f"[Telegram] Broadcast failed (sync fallback): {e}")

    # Map EventBus events to Telegram messages
    bus.subscribe(EVENTS["NODE_STARTED"], lambda n: broadcast(f"🔄 **Starting:** {getattr(n, 'task', 'Task')}"))
    bus.subscribe(EVENTS["NODE_SUCCESS"], lambda n: broadcast(f"✅ **Done:** {getattr(n, 'task', 'Task')}"))
    bus.subscribe(EVENTS["NODE_FAILED"], lambda n: broadcast(f"❌ **Failed:** {getattr(n, 'task', 'Task')}"))
    bus.subscribe(EVENTS["ROLLBACK"], lambda n: broadcast(f"⏪ **Rolling back:** {getattr(n, 'task', 'Task')}"))
    bus.subscribe(EVENTS["REPAIR"], lambda n: broadcast(f"🔧 **Attempting repair for:** {getattr(n, 'task', 'Task')}"))
    bus.subscribe(EVENTS["PLAN_CREATED"], lambda data: broadcast(f"📋 **Here's the plan:**\n{data.get('plan_summary', 'Executing objective.')}"))

