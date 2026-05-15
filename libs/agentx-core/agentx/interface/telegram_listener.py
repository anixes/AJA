import json
import os
import urllib.request
import asyncio
import logging
from agentx.config import TELEGRAM_TOKEN, TELEGRAM_ALLOWED_USER_ID

logger = logging.getLogger(__name__)

BOT_TOKEN = TELEGRAM_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN", "")

async def async_send_telegram_message(chat_id: str, message: str):
    """Non-blocking Telegram send."""
    if not BOT_TOKEN or not TELEGRAM_ALLOWED_USER_ID:
        print(f"[Telegram Bot] [MOCKED] {message}")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
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
    """Deprecated compatibility shim; UnifiedGateway handles Telegram event forwarding."""
    logger.warning(
        "legacy_telegram_listener_disabled",
        extra={"reason": "UnifiedGateway+TelegramAdapter is the primary path"},
    )
    return
