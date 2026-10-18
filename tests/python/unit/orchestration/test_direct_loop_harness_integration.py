"""
test_direct_loop_harness_integration.py - Integration tests for Propose-Probe-Compose and LessonLedger in direct_loop.
========================================================================================================================
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

from aja.orchestration.direct_loop import run_direct_loop
from aja.memory.lesson_ledger import LessonLedger, get_global_lesson_ledger
from tests.fixtures.mocks import BaseMockGateway, BaseMockRegistry, BaseMockExecutor


def test_probe_gate_intercepts_dangerous_shell_command(tmp_path: Path):
    """Verify that Propose-Probe-Compose blocks dangerous commands before reaching executor."""
    gw = BaseMockGateway(
        responses=[
            {
                "content": "I will clean the disk",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "run_shell_command",
                            "arguments": '{"command": "rm -rf /"}',
                        },
                    }
                ],
            },
            "Understood, I will not delete root.",
        ]
    )

    reg = BaseMockRegistry()
    executor = BaseMockExecutor()

    loop = asyncio.new_event_loop()
    try:
        res = loop.run_until_complete(
            run_direct_loop(
                objective="Clean up scratchpad",
                gateway=gw,
                tools_registry=reg,
                executor=executor,
                ladder_verify=True,
                check_git=False,
                workspace_root=tmp_path,
                max_turns=2,
            )
        )
    finally:
        loop.close()

    # The executor should NEVER have dispatched the dangerous command
    assert len(executor.dispatched) == 0
    assert res is not None
    assert res["status"] == "completed"


def test_probe_gate_intercepts_broken_python_write(tmp_path: Path):
    """Verify that Propose-Probe-Compose catches syntax errors before disk write."""
    gw = BaseMockGateway(
        responses=[
            {
                "content": "Writing broken python",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path": "bad.py", "content": "def broken(\\nprint(42)"}',
                        },
                    }
                ],
            },
            "I see the syntax error, aborting.",
        ]
    )

    reg = BaseMockRegistry()
    executor = BaseMockExecutor()

    loop = asyncio.new_event_loop()
    try:
        res = loop.run_until_complete(
            run_direct_loop(
                objective="Write bad python file",
                gateway=gw,
                tools_registry=reg,
                executor=executor,
                ladder_verify=True,
                check_git=False,
                workspace_root=tmp_path,
                max_turns=2,
            )
        )
    finally:
        loop.close()

    # The executor should NEVER have written the broken file
    assert len(executor.dispatched) == 0
    assert res is not None
    assert res["status"] == "completed"


def test_lesson_ledger_injection_into_prompt(tmp_path: Path, monkeypatch):
    """Verify that LessonLedger injects past lessons into the initial turn."""
    ledger_file = tmp_path / "test_ledger.json"
    ledger = LessonLedger(ledger_file)

    # Pre-seed a relevant lesson
    ledger.record_failure(
        task_objective="Deploy widget app",
        error_message="Missing port configuration",
        error_type="ConfigError",
        domain_tags=["deploy", "widget"],
        lesson_learned="Port 8080 must be explicitly bound",
        mitigation_rule="Always bind PORT=8080 when running widget app.",
    )

    # Point global ledger to this test ledger
    monkeypatch.setattr("aja.memory.lesson_ledger.get_global_lesson_ledger", lambda: ledger)

    gw = BaseMockGateway(responses=["Done successfully."])
    reg = BaseMockRegistry()
    executor = BaseMockExecutor()

    loop = asyncio.new_event_loop()
    try:
        res = loop.run_until_complete(
            run_direct_loop(
                objective="Deploy widget app to production",
                gateway=gw,
                tools_registry=reg,
                executor=executor,
                ladder_verify=False,
                check_git=False,
                workspace_root=tmp_path,
                max_turns=1,
            )
        )
    finally:
        loop.close()

    assert len(gw.calls) == 1
    prompt_history = gw.calls[0]["prompt"]
    # Check that a memory reminder was injected into prompt
    reminder_msg = next((m for m in prompt_history if "[Memory Reminder]" in m.get("content", "")), None)
    assert reminder_msg is not None
    assert "Always bind PORT=8080" in reminder_msg["content"]
