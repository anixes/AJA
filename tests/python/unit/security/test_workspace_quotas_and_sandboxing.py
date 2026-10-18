"""
test_workspace_quotas_and_sandboxing.py — Comprehensive unit tests for P3
========================================================================
Validates WorkspaceQuotas configuration, QuotaTracker pre-flight write reservations,
cumulative byte tracking, subprocess call tracking, execution timeout,
path confinement sandboxing, graceful loop halt on quota violation,
and structured ResourceAttribution emission.
Strictly model-agnostic.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from aja.config_schema import WorkspaceQuotas, SwarmSettings
from aja.runtime.execution.workspace_quotas import (
    QuotaTracker,
    QuotaExceededError,
    DiskQuotaExceededError,
    SubprocessQuotaExceededError,
    TimeQuotaExceededError,
    SandboxViolationError,
    verify_path_confinement,
)
from aja.orchestration.direct_loop import (
    DirectLoopHooks,
    ResourceAttribution,
    run_direct_loop,
)


class MockGateway:
    """Configurable mock LLM gateway for simulating agent responses and tool dispatches."""

    def __init__(self, responses: List[Any]):
        self.responses = list(responses)
        self.call_count = 0
        self.last_prompt = None

    async def chat(self, model: str, prompt: List[Dict[str, Any]], system: Optional[str] = None, tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        self.last_prompt = prompt
        if self.call_count < len(self.responses):
            res = self.responses[self.call_count]
            self.call_count += 1
            return res
        return "Task complete."


class MockRegistry:
    def get_schemas(self, interactive: bool = True, profile: str = "code") -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read file content",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_shell_command",
                    "description": "Run shell command",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                        },
                        "required": ["command"],
                    },
                },
            },
        ]


class MockToolResult:
    def __init__(self, tool: str, data: Any = None, success: bool = True, error: Optional[str] = None):
        self.tool = tool
        self.data = data
        self.success = success
        self.error = error


class MockExecutor:
    def __init__(self):
        self.dispatched_calls = []

    async def dispatch_tool_calls(self, tool_calls: List[Dict[str, Any]], trace_id: str, dry_run: bool = False) -> List[MockToolResult]:
        self.dispatched_calls.extend(tool_calls)
        results = []
        for call in tool_calls:
            t = call.get("tool", "")
            results.append(MockToolResult(tool=t, data=f"Mock result for {t}", success=True))
        return results

    async def execute_async(self, cmd: str) -> Dict[str, Any]:
        return {"status": "success", "stdout": f"Executed: {cmd}", "stderr": "", "code": 0}


# ==============================================================================
# Unit Tests: Configuration & Tracker Primitives
# ==============================================================================

def test_workspace_quotas_schema_defaults():
    """Verify default quota thresholds in config_schema.py."""
    quotas = WorkspaceQuotas()
    assert quotas.max_cumulative_write_bytes == 50 * 1024 * 1024
    assert quotas.max_single_file_write_bytes == 10 * 1024 * 1024
    assert quotas.max_subprocess_calls == 100
    assert quotas.max_total_execution_seconds == 1800.0
    assert quotas.allow_out_of_bounds_paths is False

    settings = SwarmSettings()
    assert isinstance(settings.quotas, WorkspaceQuotas)
    assert settings.quotas.max_cumulative_write_bytes == 50 * 1024 * 1024


def test_quota_tracker_single_file_limit():
    """Verify single file write quota enforcement."""
    quotas = WorkspaceQuotas(
        max_single_file_write_bytes=1000,
        max_cumulative_write_bytes=5000,
    )
    tracker = QuotaTracker(quotas=quotas)

    # 1. Under limit succeeds
    tracker.check_and_reserve_write(500)
    assert tracker.get_snapshot().disk_bytes_used == 500

    # 2. Exceeds single file limit raises DiskQuotaExceededError (is_single_file=True)
    with pytest.raises(DiskQuotaExceededError) as exc_info:
        tracker.check_and_reserve_write(1001)
    assert exc_info.value.is_single_file is True
    assert exc_info.value.limit == 1000


def test_quota_tracker_cumulative_disk_limit():
    """Verify cumulative write quota across multiple calls."""
    quotas = WorkspaceQuotas(
        max_single_file_write_bytes=2000,
        max_cumulative_write_bytes=1500,
    )
    tracker = QuotaTracker(quotas=quotas)

    # First write of 1000 bytes succeeds
    tracker.check_and_reserve_write(1000)
    assert tracker.get_snapshot().disk_bytes_used == 1000

    # Second write of 600 bytes would total 1600 > 1500 limit
    with pytest.raises(DiskQuotaExceededError) as exc_info:
        tracker.check_and_reserve_write(600)
    assert exc_info.value.is_single_file is False
    assert exc_info.value.limit == 1500


def test_quota_tracker_subprocess_calls_limit():
    """Verify maximum subprocess execution count limit."""
    quotas = WorkspaceQuotas(max_subprocess_calls=2)
    tracker = QuotaTracker(quotas=quotas)

    tracker.record_subprocess_call()
    tracker.record_subprocess_call()
    assert tracker.get_snapshot().subprocess_calls_made == 2

    # 3rd call exceeds limit
    with pytest.raises(SubprocessQuotaExceededError) as exc_info:
        tracker.record_subprocess_call()
    assert exc_info.value.limit == 2
    assert exc_info.value.current_count == 3


def test_quota_tracker_time_limit():
    """Verify execution time ceiling enforcement."""
    quotas = WorkspaceQuotas(max_total_execution_seconds=0.01)
    tracker = QuotaTracker(quotas=quotas)

    time.sleep(0.02)
    with pytest.raises(TimeQuotaExceededError) as exc_info:
        tracker.check_time_limit()
    assert exc_info.value.limit == 0.01


def test_verify_path_confinement_sandbox(tmp_path: Path):
    """Verify path confinement checks within workspace root."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # 1. Valid child path
    valid_file = workspace / "src" / "main.py"
    resolved = verify_path_confinement(valid_file, workspace_root=workspace)
    assert resolved == valid_file.resolve()

    # 2. Path escaping workspace via parent traversal
    evil_file = workspace / ".." / "evil.txt"
    with pytest.raises(SandboxViolationError) as exc_info:
        verify_path_confinement(evil_file, workspace_root=workspace)
    assert exc_info.value.workspace_root == str(workspace.resolve())

    # 3. Path allowed when allow_out_of_bounds is True
    allowed_escape = verify_path_confinement(
        evil_file,
        workspace_root=workspace,
        allow_out_of_bounds=True,
    )
    assert allowed_escape == evil_file.resolve()


# ==============================================================================
# Integration Tests: Direct Loop Sandboxing & Quotas
# ==============================================================================

def test_direct_loop_write_quota_graceful_halt(tmp_path: Path):
    """Verify direct loop halts gracefully when single-file write quota is exceeded."""
    async def _run():
        quotas = WorkspaceQuotas(max_single_file_write_bytes=50)

        # Gateway attempts to write a file larger than 50 bytes
        tool_call_payload = {
            "content": "I will write a large configuration file now.",
            "tool_calls": [
                {
                    "id": "call_write_1",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({
                            "path": str(tmp_path / "big_file.txt"),
                            "content": "A" * 200,  # 200 bytes > 50 bytes limit
                        }),
                    },
                }
            ],
        }

        gateway = MockGateway([tool_call_payload])
        executor = MockExecutor()

        quota_exceeded_events = []
        hooks = DirectLoopHooks(
            on_quota_exceeded=lambda err, snap: quota_exceeded_events.append((err, snap))
        )

        outcome = await run_direct_loop(
            "Write configuration",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=executor,
            model="test-provider:test-model",
            quotas=quotas,
            workspace_root=tmp_path,
            hooks=hooks,
            max_turns=3,
        )

        assert outcome is not None
        assert outcome["status"] == "quota_exceeded"
        assert "Single file write quota exceeded" in outcome["reason"]
        assert "attribution" in outcome
        assert outcome["attribution"]["status"] == "quota_exceeded"
        assert len(quota_exceeded_events) == 1
        assert isinstance(quota_exceeded_events[0][0], DiskQuotaExceededError)
        # Ensure executor never dispatched the offending tool call!
        assert len(executor.dispatched_calls) == 0

    asyncio.run(_run())


def test_direct_loop_sandbox_path_confinement_halt(tmp_path: Path):
    """Verify direct loop halts gracefully on path traversal escape attempt."""
    async def _run():
        quotas = WorkspaceQuotas(allow_out_of_bounds_paths=False)
        workspace = tmp_path / "project_workspace"
        workspace.mkdir()

        # Tool call targets a file escaping workspace
        tool_call_payload = {
            "content": "Reading sensitive system file.",
            "tool_calls": [
                {
                    "id": "call_read_escape",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({
                            "path": str(tmp_path / "system_passwords.txt"),  # outside workspace!
                        }),
                    },
                }
            ],
        }

        gateway = MockGateway([tool_call_payload])
        executor = MockExecutor()

        outcome = await run_direct_loop(
            "Read file safely",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=executor,
            model="test-provider:test-model",
            quotas=quotas,
            workspace_root=workspace,
            max_turns=3,
        )

        assert outcome is not None
        assert outcome["status"] == "quota_exceeded"
        assert "Sandbox confinement violation" in outcome["reason"]
        assert outcome["attribution"]["status"] == "quota_exceeded"
        assert len(executor.dispatched_calls) == 0

    asyncio.run(_run())


def test_direct_loop_subprocess_quota_halt(tmp_path: Path):
    """Verify direct loop halts gracefully when subprocess quota is exceeded."""
    async def _run():
        quotas = WorkspaceQuotas(max_subprocess_calls=1)

        # Assistant issues two bash code blocks
        response_payload = (
            "```bash\necho 'Command 1'\n```\n"
            "```bash\necho 'Command 2'\n```"
        )

        gateway = MockGateway([response_payload])
        executor = MockExecutor()

        outcome = await run_direct_loop(
            "Run commands",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=executor,
            model="test-provider:test-model",
            quotas=quotas,
            workspace_root=tmp_path,
            max_turns=3,
        )

        assert outcome is not None
        assert outcome["status"] == "quota_exceeded"
        assert "Subprocess execution quota exceeded" in outcome["reason"]
        assert outcome["attribution"]["subprocess_calls"] == 2
        assert outcome["attribution"]["status"] == "quota_exceeded"

    asyncio.run(_run())


def test_direct_loop_resource_attribution_hook(tmp_path: Path):
    """Verify on_resource_attribution callback receives full telemetry on successful completion."""
    async def _run():
        gateway = MockGateway([
            {
                "content": "Processing data",
                "usage": {"prompt_tokens": 150, "completion_tokens": 50, "total_tokens": 200},
                "tool_calls": [
                    {
                        "id": "call_write_valid",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": str(tmp_path / "result.txt"),
                                "content": "Hello World",
                            }),
                        },
                    }
                ],
            },
            "Done with processing.",
        ])
        executor = MockExecutor()

        emitted_attributions: List[ResourceAttribution] = []
        hooks = DirectLoopHooks(
            on_resource_attribution=lambda attr: emitted_attributions.append(attr)
        )

        outcome = await run_direct_loop(
            "Process data objective",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=executor,
            model="test-provider:test-model",
            workspace_root=tmp_path,
            hooks=hooks,
            max_turns=5,
        )

        assert outcome is not None
        assert outcome["status"] == "completed"
        assert len(emitted_attributions) == 1

        attr = emitted_attributions[0]
        assert isinstance(attr, ResourceAttribution)
        assert attr.model == "test-provider:test-model"
        assert attr.turns == 2
        assert attr.bytes_written == len(b"Hello World")
        assert attr.tool_calls_count == 1
        assert attr.token_usage.get("total_tokens") == 200
        assert attr.status == "completed"
        assert outcome["attribution"] == attr.to_dict()

    asyncio.run(_run())
