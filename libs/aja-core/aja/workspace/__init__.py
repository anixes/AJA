"""
AJA Workspace Subsystem
======================
"""

from aja.workspace.context import (
    WorkspaceContext,
    get_current_workspace,
    set_current_workspace,
    reset_current_workspace,
)

__all__ = [
    "WorkspaceContext",
    "get_current_workspace",
    "set_current_workspace",
    "reset_current_workspace",
    "Workspace",
    "WorkspaceRegistry",
    "get_workspace_registry",
]


def __getattr__(name: str):
    if name in ("Workspace", "WorkspaceRegistry", "get_workspace_registry"):
        import aja.workspace.manager as _mgr

        return getattr(_mgr, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

