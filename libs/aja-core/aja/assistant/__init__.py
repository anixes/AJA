"""AJA Assistant package — ambient briefings and secretary surfaces."""

from aja.assistant.briefing import (
    BRIEFING_GOAL,
    compose_briefing,
    register_briefing_jobs,
    send_briefing,
)

__all__ = [
    "BRIEFING_GOAL",
    "compose_briefing",
    "register_briefing_jobs",
    "send_briefing",
]
