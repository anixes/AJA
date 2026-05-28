import asyncio
import sys

from aja.capabilities.terminal import TerminalExec
from aja.orchestration.tools.executor import ToolExecutor
from aja.runtime import sandbox


def py_cmd(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def test_sandbox_execute_command_uses_isolated_local_when_docker_unavailable(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda: False)

    result = sandbox.execute_command(py_cmd("print('compat')"), timeout=30)

    assert result["success"] is True
    assert "compat" in result["stdout"]
    assert result["mode"] == "isolated_local"
    assert "session_id" in result


def test_sandbox_execute_command_async(monkeypatch):
    async def scenario():
        monkeypatch.setattr(sandbox, "docker_available", lambda: False)
        result = await sandbox.execute_command_async(py_cmd("print('async compat')"), timeout=30)
        assert result["success"] is True
        assert "async compat" in result["stdout"]

    asyncio.run(scenario())


def test_terminal_exec_preserves_capability_result_shape(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda: False)
    result = TerminalExec().execute({"cmd": py_cmd("print('terminal')"), "timeout": 30})

    assert result.success is True
    assert "terminal" in result.output["stdout"]
    assert result.output["mode"] == "isolated_local"


def test_tool_executor_blocks_denies_and_runs_allowed_command():
    blocked = ToolExecutor().execute("mkfs /dev/sda")
    assert blocked["status"] == "error"
    assert "blocked" in blocked["message"].lower()

    allowed = ToolExecutor().execute(py_cmd("print('tool')"))
    assert allowed["status"] == "success"
    assert "tool" in allowed["stdout"]
