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
    print("DEBUG_RESULT:", result)
    if not result.get("success"):
        print("STDOUT:", result.get("stdout"))
        print("STDERR:", result.get("stderr"))
        print("ERROR:", result.get("error"))

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
    from aja.config import CONFIG
    monkeypatch.setattr(CONFIG.swarm_settings, "auto_proceed_local", True)
    monkeypatch.setattr(sandbox, "docker_available", lambda: False)
    result = TerminalExec().execute({"cmd": py_cmd("print('terminal')"), "timeout": 30})

    assert result.success is True
    assert "terminal" in result.output["stdout"]
    assert result.output["mode"] == "isolated_local"


def test_tool_executor_blocks_denies_and_runs_allowed_command(monkeypatch):
    blocked = ToolExecutor().execute("mkfs /dev/sda")
    assert blocked["status"] == "error"
    assert "blocked" in blocked["message"].lower()

    from aja.config import CONFIG
    monkeypatch.setattr(CONFIG.swarm_settings, "auto_proceed_local", True)
    allowed = ToolExecutor().execute(py_cmd("print('tool')"))
    assert allowed["status"] == "success"
    assert "tool" in allowed["stdout"]

# Serialize process-spawning/PTY tests onto one xdist worker: concurrent ConPTY
# handle pools exhaust on Windows and wedge workers (thread-method timeouts
# cannot abort them). All heavy subprocess tests share the 'process_heavy' group.
import pytest as _pytest
pytestmark = _pytest.mark.xdist_group("process_heavy")

