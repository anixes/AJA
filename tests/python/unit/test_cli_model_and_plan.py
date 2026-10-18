"""
test_cli_model_and_plan.py - Unit tests for CLI model flag, validation, and dynamic plan tasks.
"""

import asyncio
from unittest.mock import patch, AsyncMock
import pytest
from aja.cli.registry import CommandRegistry
from aja.cli.commands.run import (
    cmd_run,
    generate_initial_plan_tasks,
    FALLBACK_INITIAL_TASKS,
)
from aja.llm import resolve_active_model_string


def test_cli_registry_model_flag_parsing():
    """Test that --model and -m flags are parsed cleanly and not passed in objective."""
    registry = CommandRegistry()

    with patch("aja.cli.commands.run.cmd_run") as mock_run:
        registry.dispatch(["run", "build", "a", "widget", "--model", "gate:glm"])
        mock_run.assert_called_once_with(
            "build a widget",
            background=False,
            dry_run=False,
            swarm=False,
            no_plan=False,
            model="gate:glm",
        )

    with patch("aja.cli.commands.run.cmd_run") as mock_run:
        registry.dispatch(["run", "test", "feature", "-m=gate:qwen"])
        mock_run.assert_called_once_with(
            "test feature",
            background=False,
            dry_run=False,
            swarm=False,
            no_plan=False,
            model="gate:qwen",
        )


def test_cli_registry_invalid_model_fails_fast():
    """Test that an unknown provider in --model causes SystemExit(2)."""
    registry = CommandRegistry()

    with pytest.raises(SystemExit) as exc_info:
        registry.dispatch(["run", "do", "task", "--model", "nonexistent_provider:model"])

    assert exc_info.value.code == 2


def test_model_precedence(monkeypatch):
    """Test precedence: explicit CLI > ENV > config > default."""
    # 1. Explicit CLI wins over everything
    monkeypatch.setenv("AJA_MODEL", "gate:glm")
    assert resolve_active_model_string("gate:qwen") == "gate:qwen"

    # 2. ENV wins over config
    assert resolve_active_model_string(None) == "gate:glm"

    # 3. Config wins when ENV unset
    monkeypatch.delenv("AJA_MODEL", raising=False)
    resolved = resolve_active_model_string(None)
    assert resolved in ("gate:qwen", "google:gemini-2.5-flash")


def test_generate_initial_plan_tasks_success():
    """Test dynamic plan generation when LLM returns valid JSON array."""
    class MockGateway:
        async def chat(self, *args, **kwargs):
            return '["Step 1: Setup", "Step 2: Build", "Step 3: Verify"]'

    async def _run():
        tasks = await generate_initial_plan_tasks("Build feature", MockGateway(), "gate:qwen")
        assert len(tasks) == 3
        assert tasks == ["Step 1: Setup", "Step 2: Build", "Step 3: Verify"]

    asyncio.run(_run())


def test_generate_initial_plan_tasks_markdown_fences():
    """Test dynamic plan generation handles ```json code fences gracefully."""
    class MockGateway:
        async def chat(self, *args, **kwargs):
            return '```json\n[\n  "Phase 1: Explore",\n  "Phase 2: Code",\n  "Phase 3: Test",\n  "Phase 4: Ship"\n]\n```'

    async def _run():
        tasks = await generate_initial_plan_tasks("Build feature", MockGateway(), "gate:qwen")
        assert len(tasks) == 4
        assert tasks[0] == "Phase 1: Explore"
        assert tasks[3] == "Phase 4: Ship"

    asyncio.run(_run())


def test_generate_initial_plan_tasks_fallback_on_invalid_schema():
    """Test fallback when LLM returns non-list or invalid length (< 3 or > 7)."""
    class MockGatewayTooFew:
        async def chat(self, *args, **kwargs):
            return '["Only one task"]'

    async def _run():
        tasks = await generate_initial_plan_tasks("Build feature", MockGatewayTooFew(), "gate:qwen")
        assert tasks == FALLBACK_INITIAL_TASKS

    asyncio.run(_run())


def test_generate_initial_plan_tasks_fallback_on_timeout():
    """Test fallback when LLM chat times out (> 10s)."""
    class MockHangingGateway:
        async def chat(self, *args, **kwargs):
            await asyncio.sleep(15.0)
            return '["Won\'t arrive"]'

    async def _run():
        tasks = await generate_initial_plan_tasks("Build feature", MockHangingGateway(), "gate:qwen")
        assert tasks == FALLBACK_INITIAL_TASKS

    async def mock_wait_for(fut, timeout=None):
        if hasattr(fut, "close"):
            fut.close()
        raise asyncio.TimeoutError()

    with patch("asyncio.wait_for", side_effect=mock_wait_for):
        asyncio.run(_run())


def test_cmd_run_background_propagates_model():
    """Test that cmd_run in background mode appends --model <model> to child process."""
    with patch("subprocess.Popen") as mock_popen:
        cmd_run("run something", background=True, model="gate:glm")
        args_passed = mock_popen.call_args[0][0]
        assert "--model" in args_passed
        idx = args_passed.index("--model")
        assert args_passed[idx + 1] == "gate:glm"
