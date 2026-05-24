import asyncio
import json
import sys
from pathlib import Path

from aja.runtime.execution import ExecutionManager, ExecutionRequest


class RaisingSink:
    def emit(self, event):
        raise RuntimeError("sink unavailable")


def py_cmd(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def make_git_project(tmp_path: Path) -> Path:
    import subprocess

    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.name", "AJA Test"], cwd=root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "add", "."], cwd=root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, text=True, check=True)
    return root


def test_execution_success_streams_and_persists_logs(tmp_path):
    async def scenario():
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root)
        result = await manager.run(
            ExecutionRequest(
                command=py_cmd("import sys; print('hello'); print('warn', file=sys.stderr)"),
                timeout=10,
            )
        )

        assert result.success is True
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert "warn" in result.stderr
        assert Path(result.manifest_path).exists()
        assert (root / ".aja" / "executions" / result.session_id / "stdout.log").read_text(encoding="utf-8").strip() == "hello"
        manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
        assert manifest["environment"]["python"]

    asyncio.run(scenario())


def test_execution_nonzero_exit_records_failure(tmp_path):
    async def scenario():
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root)
        result = await manager.run(ExecutionRequest(command=py_cmd("import sys; sys.exit(7)"), timeout=10))

        assert result.success is False
        assert result.exit_code == 7
        assert result.state == "failed"

    asyncio.run(scenario())


def test_execution_timeout_attribution(tmp_path):
    async def scenario():
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root)
        result = await manager.run(ExecutionRequest(command=py_cmd("import time; time.sleep(10)"), timeout=0.2))

        assert result.success is False
        assert result.state == "timeout"
        assert "timed out" in result.stderr.lower()

    asyncio.run(scenario())


def test_telemetry_sink_failure_does_not_fail_execution(tmp_path):
    async def scenario():
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root, event_sink=RaisingSink())
        result = await manager.run(ExecutionRequest(command=py_cmd("print('ok')"), timeout=10))

        assert result.success is True
        assert "ok" in result.stdout

    asyncio.run(scenario())
