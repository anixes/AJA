"""Pure natural-language time parsing (stdlib only: re + datetime).

parse_nl_time("tomorrow 9am") -> naive local datetime or None.
"""
import re
from datetime import datetime, timedelta
from typing import Optional

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}

_RELATIVE_RE = re.compile(
    r"\bin\s+(\d+)\s*(s|sec|secs|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|days)\b"
)
_CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?")
_BARE_HOUR_RE = re.compile(r"^(?:at\s+)?(\d{1,2})$")


def _apply_clock(text: str) -> tuple:
    """Extract (hour, minute, had_explicit_time) from text; (-1, -1, False) if none."""
    m = _CLOCK_RE.search(text)
    if not m or not (m.group(2) or m.group(3)):
        return -1, -1, False
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    meridiem = m.group(3)
    meridiem = re.sub(r"[.\s]", "", meridiem or "")
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return -1, -1, False
    return hour, minute, True


def parse_nl_time(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse a natural-language time expression into a naive-local datetime.

    Handles: clock times ("3pm", "15:00", "19:45"), day anchors ("today",
    "tonight" -> 19:00 default, "tomorrow"), weekday names (next occurrence),
    relative offsets ("in 2 hours", "in 30m") and combos ("tomorrow 9am").
    Results in the past roll forward one day. Returns None when unparseable.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip().lower()
    now = now or datetime.now()

    # Relative offsets take precedence.
    m = _RELATIVE_RE.search(s)
    if m:
        seconds = _UNITS.get(m.group(2))
        if seconds is None:
            return None
        return now + timedelta(seconds=int(m.group(1)) * seconds)

    base_day = now.replace(second=0, microsecond=0)
    has_day_anchor = False

    if re.search(r"\btonight\b", s):
        s = re.sub(r"\btonight\b", " ", s)
        base_day = base_day.replace(hour=19, minute=0)
        has_day_anchor = True
        explicit_default = (19, 0)
    else:
        explicit_default = None

    matched_tomorrow = bool(re.search(r"\btomorrow\b", s))
    if matched_tomorrow:
        s = re.sub(r"\btomorrow\b", " ", s)
        base_day = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        has_day_anchor = True
    elif re.search(r"\btoday\b", s):
        s = re.sub(r"\btoday\b", " ", s)
        base_day = now.replace(hour=9, minute=0, second=0, microsecond=0)
        has_day_anchor = True
    else:
        for name, idx in _WEEKDAYS.items():
            if re.search(rf"\b{name}\b", s):
                delta = (idx - now.weekday()) % 7 or 7  # next occurrence
                base_day = (now + timedelta(days=delta)).replace(
                    hour=9, minute=0, second=0, microsecond=0
                )
                s = re.sub(rf"\b{name}s?\b", " ", s)
                has_day_anchor = True
                break

    # Drop connector words before clock extraction.
    s = re.sub(r"\b(?:at|on)\b", " ", s)

    hour, minute, had_clock = _apply_clock(s)
    if not had_clock:
        m_bare = _BARE_HOUR_RE.match(s.strip())
        if m_bare and int(m_bare.group(1)) <= 23:
            hour, minute = int(m_bare.group(1)), 0
            had_clock = True

    if not had_clock:
        if explicit_default:
            hour, minute = explicit_default
            result = base_day.replace(hour=hour, minute=minute)
        elif not has_day_anchor:
            return None
        else:
            result = base_day
    else:
        result = base_day.replace(hour=hour, minute=minute)
        if not has_day_anchor and result <= now:
            result += timedelta(days=1)

    if result <= now:
        result += timedelta(days=1)
    return result
