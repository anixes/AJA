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
