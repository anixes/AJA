"""
concurrency.py - Safe Concurrent Async Tool Dispatch & Path Conflict Gating.
=============================================================================

Implements the Triad-approved v1 conservative batching model for AJA:
- Turn-level concurrency: parallelizes read-only tool batches with disjoint path scopes.
- Automatic serialization: falls back to strict sequential execution in declared order
  if any tool is mutating, executes code, or has prefix/subtree path overlap.
- Bounded concurrency: throttles parallel execution via asyncio.Semaphore.
- Non-blocking I/O offloading: bridges synchronous tool execution via asyncio.to_thread.
- Event Journal integration: seals tool intents and results to RunJournal with latency
  metrics and error isolation.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
from pathlib import Path
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from aja.security.command_ir import VerbClass, parse_command_to_ir

logger = logging.getLogger(__name__)


class BlockingClass(enum.Enum):
    """Categorization of tool execution profile and execution offload."""

    ASYNC_NATIVE = "async_native"
    THREAD_OFFLOAD = "thread_offload"
    PROCESS_OFFLOAD = "process_offload"


class ExecutionMode(enum.Enum):
    """Execution dispatch mode for a batch of tool calls."""

    CONCURRENT = "concurrent"
    SEQUENTIAL = "sequential"


class CancellationToken:
    """Thread-safe cooperative cancellation token for worker threads and tasks."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


def canonicalize_path(path: Union[str, Path]) -> str:
    """
    Produce a canonical, lowercase, absolute path resolving symlinks
    for reliable cross-platform path comparison.
    """
    try:
        raw = str(path).strip()
        if not raw:
            return ""
        return os.path.realpath(os.path.normcase(os.path.abspath(raw)))
    except Exception:
        return str(path)


def paths_conflict(p1: str, p2: str) -> bool:
    """
    Detect whether two canonical paths conflict.
    A conflict occurs if p1 == p2, or if either path is a parent directory
    or subtree prefix of the other.
    """
    if not p1 or not p2:
        return False
    if p1 == p2:
        return True

    # Check prefix / directory ancestry
    sep = os.sep
    p1_dir = p1 if p1.endswith(sep) else p1 + sep
    p2_dir = p2 if p2.endswith(sep) else p2 + sep

    return p1.startswith(p2_dir) or p2.startswith(p1_dir)


def extract_tool_paths(tool_name: str, args: Dict[str, Any]) -> List[str]:
    """Extract and canonicalize target paths touched by a tool call."""
    t_args = args or {}
    paths: List[str] = []

    # Common path parameter names
    for key in (
        "path",
        "file_path",
        "target_path",
        "directory",
        "dir_path",
        "search_path",
        "cwd",
    ):
        val = t_args.get(key)
        if val and isinstance(val, (str, Path)):
            canon = canonicalize_path(val)
            if canon and canon not in paths:
                paths.append(canon)

    # Shell command path extraction (from CommandIR)
    if tool_name.lower() in ("run_shell_command", "exec_command", "run_command", "bash"):
        cmd = t_args.get("command") or t_args.get("cmd") or ""
        if cmd:
            ir = parse_command_to_ir(cmd)
            for a in ir.argv[1:]:
                # If argument looks like an existing or target path
                if "/" in a or "\\" in a or "." in a:
                    canon = canonicalize_path(a)
                    if canon and canon not in paths:
                        paths.append(canon)

    return paths


READ_ONLY_TOOL_NAMES: Set[str] = {
    "read_file",
    "view_file",
    "list_directory",
    "list_dir",
    "grep_search",
    "find_files",
    "get_file_info",
    "file_info",
}


def is_read_only_tool(tool_name: str, args: Dict[str, Any]) -> bool:
    """Determine whether a tool call is guaranteed read-only with no side effects."""
    name_lower = (tool_name or "").lower()

    if name_lower in READ_ONLY_TOOL_NAMES:
        return True

    # Shell commands: query CommandIR
    if name_lower in ("run_shell_command", "exec_command", "run_command", "bash"):
        cmd = (args or {}).get("command") or (args or {}).get("cmd") or ""
        if cmd:
            ir = parse_command_to_ir(cmd)
            return ir.verb_class == VerbClass.READ_ONLY and ir.is_safe_to_probe

    return False


def determine_execution_mode(
    tool_calls: Sequence[Dict[str, Any]],
) -> Tuple[ExecutionMode, str]:
    """
    Conservative v1 Batching Rule:
    - CONCURRENT if and only if:
      1. Batch has >= 2 calls.
      2. 100% of calls are verified READ_ONLY.
      3. No pair of calls touches overlapping or nested paths.
    - Otherwise, strictly SEQUENTIAL in declared order.
    """
    if len(tool_calls) <= 1:
        return ExecutionMode.SEQUENTIAL, "single_call"

    all_paths: List[Tuple[int, str]] = []

    for idx, call in enumerate(tool_calls):
        t_name = call.get("tool") or call.get("name") or ""
        t_args = call.get("args") or {}

        # 1. Any non-read-only tool immediately demotes to sequential
        if not is_read_only_tool(t_name, t_args):
            return (
                ExecutionMode.SEQUENTIAL,
                f"mutating_or_exec_tool_{t_name}_at_index_{idx}",
            )

        # 2. Extract paths for conflict detection
        paths = extract_tool_paths(t_name, t_args)
        for p in paths:
            all_paths.append((idx, p))

    # 3. Check pairwise path conflicts across calls
    for i in range(len(all_paths)):
        for j in range(i + 1, len(all_paths)):
            idx_a, path_a = all_paths[i]
            idx_b, path_b = all_paths[j]
            if idx_a != idx_b and paths_conflict(path_a, path_b):
                return (
                    ExecutionMode.SEQUENTIAL,
                    f"path_conflict_between_call_{idx_a}_and_{idx_b}",
                )

    return ExecutionMode.CONCURRENT, "disjoint_read_only_batch"


class ConcurrentToolBatcher:
    """
    Orchestrates turn-level tool execution with concurrency gating,
    asyncio.to_thread bridging, and RunJournal telemetry.
    """

    def __init__(self, max_concurrency: int = 4) -> None:
        self.max_concurrency = max(1, max_concurrency)
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    async def execute_call(
        self,
        call: Dict[str, Any],
        dispatch_fn: Callable[[Dict[str, Any]], Any],
        journal: Optional[Any] = None,
        run_id: Optional[str] = None,
        step_index: int = 0,
        token: Optional[CancellationToken] = None,
    ) -> Any:
        """
        Execute an individual tool call, optionally recording intent and result to journal.
        """
        t_name = call.get("tool") or call.get("name") or "unknown"
        t_args = call.get("args") or {}

        # 1. Emit tool_intent to RunJournal if active
        if journal and run_id:
            try:
                journal.append_event(
                    run_id=run_id,
                    event_type="tool_intent",
                    step_index=step_index,
                    payload={"tool": t_name, "args": t_args},
                )
            except Exception as e:
                logger.debug("Failed to emit tool_intent to journal: %s", e)

        start_time = time.monotonic()
        error_msg: Optional[str] = None
        result: Any = None

        try:
            # Check if dispatch_fn is an async coroutine function
            if asyncio.iscoroutinefunction(dispatch_fn):
                result = await dispatch_fn(call)
            else:
                # Wrap synchronous tool execution in thread pool
                result = await asyncio.to_thread(dispatch_fn, call)
        except Exception as e:
            error_msg = str(e)
            result = SimpleNamespace(
                tool=t_name,
                success=False,
                error=error_msg,
                data=None,
                stderr=error_msg,
            )

        duration_ms = (time.monotonic() - start_time) * 1000.0

        # 2. Emit tool_result to RunJournal if active
        if journal and run_id:
            try:
                success = bool(getattr(result, "success", True))
                if error_msg is not None:
                    success = False
                journal.append_event(
                    run_id=run_id,
                    event_type="tool_result",
                    step_index=step_index,
                    payload={
                        "tool": t_name,
                        "success": success,
                        "duration_ms": round(duration_ms, 2),
                        "error": error_msg,
                    },
                )
            except Exception as e:
                logger.debug("Failed to emit tool_result to journal: %s", e)

        return result

    async def execute_batch(
        self,
        tool_calls: Sequence[Dict[str, Any]],
        dispatch_fn: Callable[[Dict[str, Any]], Any],
        journal: Optional[Any] = None,
        run_id: Optional[str] = None,
        step_index: int = 0,
    ) -> List[Any]:
        """
        Execute a batch of tool calls using conservative concurrency gating.
        Preserves the original declared order in the returned results list.
        """
        if not tool_calls:
            return []

        mode, reason = determine_execution_mode(tool_calls)
        logger.debug(
            "[ConcurrentToolBatcher] Batch of %d calls dispatched via %s mode (%s)",
            len(tool_calls),
            mode.value,
            reason,
        )

        # 1. Concurrent Execution
        if mode == ExecutionMode.CONCURRENT:
            token = CancellationToken()

            async def bounded_exec(c: Dict[str, Any]) -> Any:
                async with self._semaphore:
                    return await self.execute_call(
                        call=c,
                        dispatch_fn=dispatch_fn,
                        journal=journal,
                        run_id=run_id,
                        step_index=step_index,
                        token=token,
                    )

            # Gather with exception isolation: one failing tool does not cancel siblings
            results = await asyncio.gather(
                *[bounded_exec(call) for call in tool_calls],
                return_exceptions=True,
            )

            # Normalize any uncaught BaseExceptions
            clean_results = []
            for idx, r in enumerate(results):
                if isinstance(r, BaseException):
                    call = tool_calls[idx]
                    t_name = call.get("tool") or call.get("name") or "unknown"
                    clean_results.append(
                        SimpleNamespace(
                            tool=t_name,
                            success=False,
                            error=str(r),
                            data=None,
                            stderr=str(r),
                        )
                    )
                else:
                    clean_results.append(r)
            return clean_results

        # 2. Strict Sequential Execution (in declared order)
        sequential_results: List[Any] = []
        for call in tool_calls:
            res = await self.execute_call(
                call=call,
                dispatch_fn=dispatch_fn,
                journal=journal,
                run_id=run_id,
                step_index=step_index,
            )
            sequential_results.append(res)

        return sequential_results
