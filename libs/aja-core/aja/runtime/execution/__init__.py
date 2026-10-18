"""Canonical execution runtime for AJA.

The public surface intentionally stays small: typed request/result contracts,
an ExecutionManager, and a process-wide default manager used by compatibility
wrappers.
"""

from aja.runtime.execution.contracts import (
    ExecutionManifest,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSession,
    ExecutionState,
    ExecutionStreamEvent,
    ProcessSnapshot,
    WorkspaceDiff,
    WorkspaceSnapshot,
)


def __getattr__(name: str):
    if name in ("ExecutionManager", "get_default_execution_manager"):
        from aja.runtime.execution.manager import (
            ExecutionManager,
            get_default_execution_manager,
        )
        return ExecutionManager if name == "ExecutionManager" else get_default_execution_manager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ExecutionManifest",
    "ExecutionManager",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionSession",
    "ExecutionState",
    "ExecutionStreamEvent",
    "ProcessSnapshot",
    "WorkspaceDiff",
    "WorkspaceSnapshot",
    "get_default_execution_manager",
]
