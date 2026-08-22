import asyncio
import sys
from pathlib import Path

from aja.runtime.execution import ExecutionManager, ExecutionRequest

from test_execution_runtime import make_git_project


def py_cmd(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def test_cancel_running_execution(tmp_path: Path):
    async def scenario():
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root)
        session = await manager.start(ExecutionRequest(command=py_cmd("import time; time.sleep(4)"), timeout=30))
        await asyncio.sleep(0.2)

        await asyncio.gather(manager.cancel(session.session_id), manager.cancel(session.session_id), manager.cancel(session.session_id))
        result = await manager.wait(session.session_id)

        assert result.success is False
        assert result.state in {"cancelled", "failed"}

    asyncio.run(scenario())


def test_timeout_race_settles_once(tmp_path: Path):
    async def scenario():
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root)
        session = await manager.start(ExecutionRequest(command=py_cmd("import time; time.sleep(4)"), timeout=0.2))
        await asyncio.sleep(0.1)
        await manager.cancel(session.session_id)
        result = await manager.wait(session.session_id)

        assert result.state in {"cancelled", "timeout", "failed"}
        assert result.exit_code == -1

    asyncio.run(scenario())


def test_cleanup_sweep_is_scoped_to_execution_workspaces(tmp_path: Path):
    root = make_git_project(tmp_path)
    from aja.runtime.execution.workspace import WorkspaceManager
    base_dir = tmp_path / "workspaces"
    wm = WorkspaceManager(project_root=root, base_dir=base_dir)
    manager = ExecutionManager(project_root=root, workspace_manager=wm)
    stale = base_dir / "exec-stale"
    stale.mkdir(parents=True)
    (stale / "file.txt").write_text("old", encoding="utf-8")

    removed = manager.cleanup_stale()

    assert "exec-stale" in removed
    assert not stale.exists()

# Serialize process-spawning/PTY tests onto one xdist worker: concurrent ConPTY
# handle pools exhaust on Windows and wedge workers (thread-method timeouts
# cannot abort them). All heavy subprocess tests share the 'process_heavy' group.
import pytest as _pytest
pytestmark = _pytest.mark.xdist_group("process_heavy")

