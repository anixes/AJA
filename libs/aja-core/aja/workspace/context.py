"""
AJA Workspace Context
====================
Provides thread-safe, coroutine-isolated workspace execution context
using Python's contextvars mechanism. Eliminates global variable mutation
and guarantees race-condition free multi-workspace concurrency.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
import os


@dataclass
class WorkspaceContext:
    """
    Encapsulates all runtime parameters for an active workspace.
    """
    id: str
    name: str
    path: Path
    storage_dir: Path
    config_overrides: Dict[str, Any] = field(default_factory=dict)
    is_ephemeral: bool = False

    def __post_init__(self):
        self.path = Path(self.path).resolve()
        self.storage_dir = Path(self.storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    @property
    def memory_dir(self) -> Path:
        p = self.storage_dir / "memory"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def baton_dir(self) -> Path:
        p = self.storage_dir / "batons"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        p = self.storage_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p


# ContextVar for coroutine-isolated workspace binding
_current_workspace_var: ContextVar[Optional[WorkspaceContext]] = ContextVar(
    "current_workspace_context", default=None
)


def get_current_workspace() -> Optional[WorkspaceContext]:
    """Retrieve the active workspace context for the current async task / thread."""
    return _current_workspace_var.get()


def set_current_workspace(ctx: WorkspaceContext) -> Any:
    """Set the active workspace context and return the reset token."""
    return _current_workspace_var.set(ctx)


def reset_current_workspace(token: Any) -> None:
    """Reset the active workspace context using the previous token."""
    _current_workspace_var.reset(token)
