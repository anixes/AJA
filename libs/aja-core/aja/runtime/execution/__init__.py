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
from aja.runtime.execution.manager import ExecutionManager, get_default_execution_manager

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
