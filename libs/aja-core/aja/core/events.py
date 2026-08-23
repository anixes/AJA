"""Typed conversation-core events.

Adapters iterate ``AsyncIterator[CoreEvent]`` yielded by
``ConversationCore.handle`` and render each event natively per platform.
Stdlib-only: this module must stay importable without any AJA heavy deps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Union

__all__ = [
    "Delta",
    "ToolStarted",
    "ToolFinished",
    "ApprovalRequested",
    "Error",
    "Final",
    "CoreEvent",
]


@dataclass
class Delta:
    """Incremental text chunk (streaming token or intermediate answer part)."""

    text: str


@dataclass
class ToolStarted:
    """A tool invocation has been dispatched."""

    name: str
    args_summary: str = ""


@dataclass
class ToolFinished:
    """A tool invocation completed."""

    name: str
    success: bool
    duration_ms: float = 0


@dataclass
class ApprovalRequested:
    """An operator approval gate was raised (render approve/reject UI)."""

    approval_id: str
    reason: str


@dataclass
class Error:
    """Structured failure surfaced to the adapter."""

    code: str
    message: str
    recoverable: bool = True


@dataclass
class Final:
    """Terminal event of a turn: the complete reply plus optional artifacts."""

    text: str
    artifacts: Dict[str, Any] = field(default_factory=dict)


CoreEvent = Union[
    Delta,
    ToolStarted,
    ToolFinished,
    ApprovalRequested,
    Error,
    Final,
]
