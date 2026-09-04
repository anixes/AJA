"""Audio transcription module for AJA Gateway.

Transcribes Telegram voice notes and audio messages using Google Gemini
multimodal audio or OpenAI Whisper, with automatic fallback and local saving.
"""
import asyncio
import base64
import logging
import os
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)


async def transcribe_telegram_audio(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    filename: Optional[str] = None,
) -> Optional[str]:
    """Transcribe inbound audio bytes into text.

    Tries Gemini Multimodal Audio first, then OpenAI Whisper API.
    Returns transcript string on success, or None if transcription is unavailable.
    """
    if not audio_bytes:
        return None

    # 1. Try Google Gemini Multimodal Audio
    gemini_key = (
        os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("AI_KEY")
    )
    if gemini_key:
        try:
            transcript = await _transcribe_with_gemini(audio_bytes, mime_type, gemini_key)
            if transcript:
                return transcript
        except Exception as e:
            logger.warning("Gemini audio transcription failed: %s", e)

    # 2. Try OpenAI Whisper API
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            transcript = await _transcribe_with_whisper(
                audio_bytes, openai_key, filename or "voice.ogg"
            )
            if transcript:
                return transcript
        except Exception as e:
            logger.warning("OpenAI Whisper transcription failed: %s", e)

    # 3. Try Local Whisper if installed
    try:
        transcript = await asyncio.to_thread(_transcribe_with_local_whisper, audio_bytes)
        if transcript:
            return transcript
    except Exception:
        pass

    return None


async def _transcribe_with_gemini(
    audio_bytes: bytes, mime_type: str, api_key: str
) -> Optional[str]:
    """Transcribes audio using Gemini 2.5 Flash multimodal input."""
    # Ensure mime_type is valid for Gemini (audio/ogg, audio/mp3, audio/wav, etc.)
    norm_mime = mime_type.lower()
    if "ogg" in norm_mime or "oga" in norm_mime:
        norm_mime = "audio/ogg"
    elif "mp3" in norm_mime or "mpeg" in norm_mime:
        norm_mime = "audio/mp3"
    elif "wav" in norm_mime:
        norm_mime = "audio/wav"
    elif "m4a" in norm_mime or "mp4" in norm_mime:
        norm_mime = "audio/mp4"
    else:
        norm_mime = "audio/ogg"

    b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": norm_mime,
                            "data": b64_audio,
                        }
                    },
                    {
                        "text": (
                            "Transcribe this audio precisely into text. "
                            "Output ONLY the spoken words with appropriate punctuation and capitalization. "
                            "Do not include any prefixes, quotes, explanations, or commentary."
                        )
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
        },
    }

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Gemini transcription HTTP %s: %s", resp.status, body[:200])
                return None
            data = await resp.json()
            candidates = data.get("candidates") or []
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()
    return None


async def _transcribe_with_whisper(
    audio_bytes: bytes, api_key: str, filename: str
) -> Optional[str]:
    """Transcribes audio using OpenAI Whisper API endpoint."""
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    data = aiohttp.FormData()
    data.add_field("model", "whisper-1")
    data.add_field(
        "file",
        audio_bytes,
        filename=filename,
        content_type="application/octet-stream",
    )

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, data=data) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Whisper transcription HTTP %s: %s", resp.status, body[:200])
                return None
            result = await resp.json()
            return result.get("text", "").strip()


def _transcribe_with_local_whisper(audio_bytes: bytes) -> Optional[str]:
    """Fallback to locally installed whisper package if present."""
    import tempfile
    import whisper  # raises ImportError if not installed

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        model = whisper.load_model("base")
        result = model.transcribe(tmp_path)
        return result.get("text", "").strip()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
