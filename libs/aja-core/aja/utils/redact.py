"""
Secret redaction helpers for safe console/log output.

Single canonical implementation so every module that echoes commands,
tool arguments, or provider payloads can avoid leaking credentials.
"""

import re
from typing import Any

# Ordered patterns: most specific first. Each replacement keeps a readable
# prefix where applicable ("Bearer ***") and masks the credential itself.
_REDACTION_PATTERNS = [
    # OpenAI-style keys and similar sk- tokens
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    # Bearer / token authorization headers
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|authorization)\s*[=:]\s*)(?!\s*$)\S+"),
    # Google API key query parameters
    re.compile(r"([?&]key=)[^&'\s]+"),
    # GitHub tokens
    re.compile(r"(?:gh[pousr]_[A-Za-z0-9_]{20,})"),
    # Telegram bot tokens (e.g. 1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx)
    # URL-embedded form first (api.telegram.org/bot<token>/) so the whole
    # bot<token> segment masks as a unit; no leading \b on the bare form
    # since "bot" is alphanumeric and a word boundary never matches there.
    re.compile(r"bot\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
    re.compile(r"\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
    # Slack tokens (xoxb-/xoxp-/xoxa-/xoxr-/xoxs-...)
    re.compile(r"\bxox[aebprs]-[A-Za-z0-9\-]{10,}"),
    # Discord bot tokens (three dot-separated base64url segments)
    re.compile(r"\b[A-Za-z0-9_\-]{24}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}"),
    # AWS access key ids
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

_MASK = "***REDACTED***"


def redact_secrets(value: Any) -> str:
    """
    Returns a string copy of *value* with credential-looking substrings masked.

    Safe to call on any object; non-string inputs are converted with str().
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    for pattern in _REDACTION_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) or "") + _MASK if m.groups() else _MASK, text)
    return text
