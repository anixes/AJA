"""MEDIA: file delivery + error policy for Telegram replies.

Implements Telegram Tier 2 items 6-7 from docs/plans/TELEGRAM_UX_UPGRADE.md:

1. MEDIA: tag extraction — agent replies may contain ``MEDIA:/path/to/file``
   lines. Each referenced file is shipped as a native Telegram document and
   the tag is stripped from the visible text. Supported extensions mirror
   Hermes' list (images, audio, video, docs, archives); unknown extensions
   are still sent as documents (Telegram accepts arbitrary binaries).

2. Error policy — friendly, deduplicated error bubbles instead of raw
   tracebacks. ``error_policy``: "always" | "once" | "silent". "once" sends
   each unique error message once per cooldown window.

All delivery helpers degrade silently on failure (cosmetic surface) EXCEPT
document sends, which report failure into the returned summary so the caller
can decide whether to retry.
"""

import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

MEDIA_TAG_RE = re.compile(r"^\s*MEDIA:(.+?)\s*$", re.MULTILINE)

# Extensions deliverable as native attachments. Anything NOT on this list is
# still sent as a document — the list exists to catch typos early (e.g.
# "MEDIA:C:\..." path fragments that are actually prose).
KNOWN_EXTENSIONS = {
    # images
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg",
    # audio
    ".mp3", ".wav", ".ogg", ".m4a", ".opus", ".flac", ".aac",
    # video
    ".mp4", ".mov", ".webm", ".mkv", ".avi",
    # documents
    ".pdf", ".txt", ".md", ".csv", ".json", ".xml", ".html", ".yaml",
    ".yml", ".log",
    # office
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
    # archives
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
}

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024  # Telegram bot API document cap


def extract_media_tags(text: str) -> Tuple[str, List[Path]]:
    """Strips ``MEDIA:<path>`` lines from *text*.

    Returns ``(clean_text, [Path, ...])``. Nonexistent paths are dropped with
    a warning (they cannot be delivered). Duplicate paths collapse to one.
    """
    if not text or "MEDIA:" not in text:
        return text, []

    paths: List[Path] = []
    seen: set = set()

    def _collect(match: re.Match) -> str:
        raw = match.group(1).strip().strip("\"'")
        p = Path(raw)
        key = str(p)
        if key not in seen:
            seen.add(key)
            if p.is_file():
                paths.append(p)
            else:
                logger.warning("MEDIA: tag references missing file: %s", p)
        return ""  # remove the line entirely

    clean = MEDIA_TAG_RE.sub(_collect, text)
    # Collapse the blank left by removal; keep intentional blank lines intact.
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip("\n")
    return clean, paths


class ErrorPolicy:
    """Deduplicating error-reply gate.

    - always: every error surfaces (default behavior before this class existed)
    - once: each unique error message is sent once per cooldown window
    - silent: never send
    """

    COOLDOWN_SECONDS = 300

    def __init__(self, policy: str = "always"):
        policy = (policy or "always").lower()
        if policy not in ("always", "once", "silent"):
            logger.warning("Unknown errorPolicy %r; using 'always'", policy)
            policy = "always"
        self.policy = policy
        self._seen: dict = {}  # hash -> last sent monotonic time

    def should_send(self, error_text: str) -> bool:
        if self.policy == "silent":
            return False
        if self.policy == "always":
            return True
        digest = hashlib.sha256(error_text.strip().encode()).hexdigest()
        now = time.monotonic()
        last = self._seen.get(digest)
        if last is not None and (now - last) < self.COOLDOWN_SECONDS:
            return False
        self._seen[digest] = now
        return True


def format_error_reply(exc: Exception, context: str = "") -> str:
    """Human-friendly one-liner instead of a raw traceback."""
    label = f"{context}: " if context else ""
    detail = str(exc).strip() or exc.__class__.__name__
    return f"⚠️ {label}something went wrong ({detail[:200]}). Try again or /status."


async def send_documents(
    bot: Any,
    chat_id: str,
    paths: List[Path],
    caption: str = "",
) -> Tuple[List[Path], List[str]]:
    """Sends files as Telegram documents. Returns (delivered, failed_paths).

    Oversized files (> MAX_DOCUMENT_BYTES) are skipped as failed rather than
    attempted, since Telegram rejects them server-side anyway.
    """
    delivered: List[Path] = []
    failed: List[str] = []
    for p in paths:
        try:
            size = p.stat().st_size
            if size > MAX_DOCUMENT_BYTES:
                failed.append(f"{p.name}: too large ({size // (1024*1024)} MB)")
                continue
            await bot.send_document(
                chat_id=chat_id,
                document=open(p, "rb"),
                caption=caption if p is paths[-1] else "",
            )
            delivered.append(p)
        except Exception as e:
            logger.warning("Document send failed for %s: %s", p, e)
            failed.append(f"{p.name}: {e}")
    return delivered, failed
