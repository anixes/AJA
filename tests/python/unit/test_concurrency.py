"""
test_concurrency.py - Unit test suite for Safe Concurrent Tool Execution & Gating.
==================================================================================

Validates Step 4.3 requirements:
1. Canonical path normalization and conflict detection (exact match, prefix/subtree overlap).
2. Read-only and mutating tool classification (including CommandIR integration).
3. Conservative batching rule: parallelize iff >=2 calls, 100% read-only, disjoint paths.
4. ConcurrentToolBatcher bounded execution, non-blocking offloading, and order preservation.
5. Error isolation: individual tool exceptions do not kill concurrent sibling tasks.
6. RunJournal event telemetry: tool_intent and tool_result sealing with latencies.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from aja.orchestration.concurrency import (
    CancellationToken,
    ConcurrentToolBatcher,
    ExecutionMode,
    canonicalize_path,
    determine_execution_mode,
    extract_tool_paths,
    is_read_only_tool,
    paths_conflict,
)
from aja.orchestration.run_journal import RunJournal


def test_canonicalize_path(tmp_path: Path) -> None:
    """Validate cross-platform normalization of paths."""
    assert canonicalize_path("") == ""
    assert canonicalize_path("   ") == ""

    sub = tmp_path / "subdir"
    sub.mkdir()
    f = sub / "test.txt"
    f.write_text("hello", encoding="utf-8")

    canon = canonicalize_path(str(f))
    assert os.path.isabs(canon)
    assert canon == os.path.realpath(os.path.normcase(os.path.abspath(str(f))))


def test_paths_conflict_logic(tmp_path: Path) -> None:
    """Validate prefix and ancestry conflict detection."""
    p1 = canonicalize_path(tmp_path / "dir1" / "file.txt")
    p2 = canonicalize_path(tmp_path / "dir1" / "file.txt")
    p3 = canonicalize_path(tmp_path / "dir1")
    p4 = canonicalize_path(tmp_path / "dir2" / "file.txt")
    p5 = canonicalize_path(tmp_path / "dir1_sibling" / "file.txt")

    # Identical paths conflict
    assert paths_conflict(p1, p2) is True

    # Parent directory vs file in that directory conflicts
    assert paths_conflict(p1, p3) is True
    assert paths_conflict(p3, p1) is True

    # Completely disjoint directories do not conflict
    assert paths_conflict(p1, p4) is False

    # Name prefix that is not a directory boundary should not conflict
    # e.g., 'dir1' vs 'dir1_sibling'
    assert paths_conflict(p3, p5) is False

    # Empty inputs never conflict
    assert paths_conflict("", p1) is False
    assert paths_conflict(p1, "") is False


def test_extract_tool_paths(tmp_path: Path) -> None:
    """Validate path extraction across various tool argument signatures."""
    file_a = str(tmp_path / "a.txt")
    file_b = str(tmp_path / "b.txt")

    # Standard tool arguments
    paths = extract_tool_paths("read_file", {"path": file_a})
    assert len(paths) == 1
    assert paths[0] == canonicalize_path(file_a)

    paths = extract_tool_paths("grep_search", {"search_path": file_a, "target_path": file_b})
    assert len(paths) == 2
    assert canonicalize_path(file_a) in paths
    assert canonicalize_path(file_b) in paths

    # Shell command arguments
    paths = extract_tool_paths("run_shell_command", {"command": f"cat {file_a}"})
    assert len(paths) >= 1
    assert canonicalize_path(file_a) in paths


def test_is_read_only_tool() -> None:
    """Validate read-only classification of native and shell commands."""
    # Native read tools
    assert is_read_only_tool("read_file", {"path": "a.txt"}) is True
    assert is_read_only_tool("list_directory", {"path": "."}) is True
    assert is_read_only_tool("grep_search", {"query": "foo"}) is True
    assert is_read_only_tool("view_file", {"path": "a.py"}) is True

    # Native mutating tools
    assert is_read_only_tool("write_file", {"path": "a.txt"}) is False
    assert is_read_only_tool("delete_file", {"path": "a.txt"}) is False
    assert is_read_only_tool("replace_file_content", {"path": "a.txt"}) is False

    # Shell read commands
    assert is_read_only_tool("run_shell_command", {"command": "cat README.md"}) is True
    assert is_read_only_tool("run_shell_command", {"command": "git status"}) is True

    # Shell mutating/code execution commands
    assert is_read_only_tool("run_shell_command", {"command": "rm -rf build"}) is False
    assert is_read_only_tool("run_shell_command", {"command": "python script.py"}) is False
    assert is_read_only_tool("run_shell_command", {"command": "git push origin main"}) is False


def test_determine_execution_mode(tmp_path: Path) -> None:
    """Validate the conservative v1 batching decision tree."""
    f1 = str(tmp_path / "f1.txt")
    f2 = str(tmp_path / "f2.txt")
    f3 = str(tmp_path / "f3.txt")

    # Single call -> SEQUENTIAL
    mode, reason = determine_execution_mode([{"tool": "read_file", "args": {"path": f1}}])
    assert mode == ExecutionMode.SEQUENTIAL
    assert reason == "single_call"

    # Multiple disjoint read-only calls -> CONCURRENT
    mode, reason = determine_execution_mode([
        {"tool": "read_file", "args": {"path": f1}},
        {"tool": "read_file", "args": {"path": f2}},
        {"tool": "read_file", "args": {"path": f3}},
    ])
    assert mode == ExecutionMode.CONCURRENT
    assert reason == "disjoint_read_only_batch"

    # Any mutating tool -> SEQUENTIAL
    mode, reason = determine_execution_mode([
        {"tool": "read_file", "args": {"path": f1}},
        {"tool": "write_file", "args": {"path": f2, "content": "hi"}},
    ])
    assert mode == ExecutionMode.SEQUENTIAL
    assert "mutating_or_exec_tool" in reason

    # Path conflict between read-only tools (same file) -> SEQUENTIAL
    mode, reason = determine_execution_mode([
        {"tool": "read_file", "args": {"path": f1}},
        {"tool": "read_file", "args": {"path": f1}},
    ])
    assert mode == ExecutionMode.SEQUENTIAL
    assert "path_conflict" in reason

    # Path conflict between parent directory and child file -> SEQUENTIAL
    dir_path = str(tmp_path / "subdir")
    child_file = str(tmp_path / "subdir" / "child.txt")
    mode, reason = determine_execution_mode([
        {"tool": "list_directory", "args": {"path": dir_path}},
        {"tool": "read_file", "args": {"path": child_file}},
    ])
    assert mode == ExecutionMode.SEQUENTIAL
    assert "path_conflict" in reason


def test_batcher_concurrent_speedup(tmp_path: Path) -> None:
    """Verify concurrent batch executes in parallel, achieving measurable speedup."""
    async def _test() -> None:
        batcher = ConcurrentToolBatcher(max_concurrency=4)

        async def mock_async_dispatch(call: Dict[str, Any]) -> Any:
            # Simulate I/O sleep
            await asyncio.sleep(0.06)
            return SimpleNamespace(
                tool=call["tool"],
                success=True,
                data=f"read_{call['args']['path']}",
                error=None,
            )

        f1 = str(tmp_path / "1.txt")
        f2 = str(tmp_path / "2.txt")
        f3 = str(tmp_path / "3.txt")

        calls = [
            {"tool": "read_file", "args": {"path": f1}},
            {"tool": "read_file", "args": {"path": f2}},
            {"tool": "read_file", "args": {"path": f3}},
        ]

        t0 = time.monotonic()
        results = await batcher.execute_batch(calls, mock_async_dispatch)
        elapsed = time.monotonic() - t0

        assert len(results) == 3
        # In parallel, total time ~ 0.06s-0.12s, definitely < 0.16s (sequential would be 0.18s+)
        assert elapsed < 0.16, f"Elapsed time was {elapsed}s, expected parallel execution"

        # Order must be strictly preserved
        assert results[0].data == f"read_{f1}"
        assert results[1].data == f"read_{f2}"
        assert results[2].data == f"read_{f3}"

    asyncio.run(_test())


def test_batcher_synchronous_offloading(tmp_path: Path) -> None:
    """Verify synchronous blocking callables are offloaded to thread pool without stalling."""
    async def _test() -> None:
        batcher = ConcurrentToolBatcher(max_concurrency=2)

        def mock_sync_dispatch(call: Dict[str, Any]) -> Any:
            time.sleep(0.04)
            return SimpleNamespace(
                tool=call["tool"],
                success=True,
                data="sync_result",
                error=None,
            )

        f1 = str(tmp_path / "1.txt")
        f2 = str(tmp_path / "2.txt")

        calls = [
            {"tool": "read_file", "args": {"path": f1}},
            {"tool": "read_file", "args": {"path": f2}},
        ]

        t0 = time.monotonic()
        results = await batcher.execute_batch(calls, mock_sync_dispatch)
        elapsed = time.monotonic() - t0

        assert len(results) == 2
        assert elapsed < 0.075  # Both ran in worker threads concurrently
        assert results[0].success is True
        assert results[1].success is True

    asyncio.run(_test())


def test_batcher_error_isolation(tmp_path: Path) -> None:
    """Verify an exception in one concurrent tool does not crash or abort siblings."""
    async def _test() -> None:
        batcher = ConcurrentToolBatcher(max_concurrency=4)

        async def mock_faulty_dispatch(call: Dict[str, Any]) -> Any:
            if "fail" in call["args"]["path"]:
                raise RuntimeError("Disk read error on sector 42")
            await asyncio.sleep(0.02)
            return SimpleNamespace(tool=call["tool"], success=True, data="ok", error=None)

        calls = [
            {"tool": "read_file", "args": {"path": str(tmp_path / "pass1.txt")}},
            {"tool": "read_file", "args": {"path": str(tmp_path / "fail.txt")}},
            {"tool": "read_file", "args": {"path": str(tmp_path / "pass2.txt")}},
        ]

        results = await batcher.execute_batch(calls, mock_faulty_dispatch)
        assert len(results) == 3

        assert results[0].success is True
        assert results[0].data == "ok"

        # Failing tool should be captured as failure namespace, not an uncaught exception
        assert results[1].success is False
        assert "Disk read error on sector 42" in str(results[1].error)

        assert results[2].success is True
        assert results[2].data == "ok"

    asyncio.run(_test())


def test_batcher_journal_telemetry(tmp_path: Path) -> None:
    """Verify tool_intent and tool_result events are sealed in RunJournal."""
    async def _test() -> None:
        journal = RunJournal(db_path=":memory:")
        run_id = "test-run-concurrent"
        journal.start_run(run_id=run_id, task="test concurrent batch telemetry")

        batcher = ConcurrentToolBatcher(max_concurrency=2)

        async def mock_dispatch(call: Dict[str, Any]) -> Any:
            return SimpleNamespace(tool=call["tool"], success=True, data="data", error=None)

        calls = [
            {"tool": "read_file", "args": {"path": str(tmp_path / "a.txt")}},
            {"tool": "read_file", "args": {"path": str(tmp_path / "b.txt")}},
        ]

        results = await batcher.execute_batch(
            tool_calls=calls,
            dispatch_fn=mock_dispatch,
            journal=journal,
            run_id=run_id,
            step_index=3,
        )
        assert len(results) == 2

        events = journal.get_events(run_id)
        # Expected: 1 run_started + 2 tool_intent + 2 tool_result = 5 events
        assert len(events) == 5

        intents = [e for e in events if e.event_type == "tool_intent"]
        tool_results = [e for e in events if e.event_type == "tool_result"]

        assert len(intents) == 2
        assert len(tool_results) == 2

        for r in tool_results:
            assert r.step_index == 3
            assert r.payload["success"] is True
            assert "duration_ms" in r.payload

        # Cryptographic hash chain verification
        verified, reason = journal.verify_run_integrity(run_id)
        assert verified is True
        assert reason is None

    asyncio.run(_test())


def test_cancellation_token() -> None:
    """Verify thread-safe cooperative cancellation token behavior."""
    token = CancellationToken()
    assert token.is_cancelled is False
    token.cancel()
    assert token.is_cancelled is True
