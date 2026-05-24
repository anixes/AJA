import json
import os
import asyncio
import logging
import httpx
from aja.config import TELEGRAM_TOKEN, TELEGRAM_ALLOWED_USER_ID

logger = logging.getLogger(__name__)

BOT_TOKEN = TELEGRAM_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN", "")

async def async_send_telegram_message(chat_id: str, message: str):
    """Asynchronous, non-blocking Telegram send using httpx."""
    if not BOT_TOKEN or not TELEGRAM_ALLOWED_USER_ID:
        print(f"[Telegram Bot] [MOCKED] {message}")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
    except Exception as e:
        logger.error(f"[Telegram] Async send failed: {e}")

def setup_telegram_listener():
    """Deprecated compatibility shim; UnifiedGateway handles Telegram event forwarding."""
    logger.warning(
        "legacy_telegram_listener_disabled",
        extra={"reason": "UnifiedGateway+TelegramAdapter is the primary path"},
    )
    return
