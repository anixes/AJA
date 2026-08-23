"""aja.core — the single conversation brain (ConversationCore) and its typed
event contract. Import-time pure: stdlib + stdlib-pure AJA envelopes only."""
from aja.core.events import (
    ApprovalRequested,
    CoreEvent,
    Delta,
    Error,
    Final,
    ToolFinished,
    ToolStarted,
)

__all__ = [
    "ApprovalRequested",
    "CoreEvent",
    "Delta",
    "Error",
    "Final",
    "ToolFinished",
    "ToolStarted",
]


def __getattr__(name):
    # Lazy so `import aja.core` stays free of any transitive weight.
    if name == "ConversationCore":
        from aja.core.conversation import ConversationCore

        return ConversationCore
    raise AttributeError(name)
