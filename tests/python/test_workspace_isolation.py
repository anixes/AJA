import asyncio
import sys
from pathlib import Path

from aja.runtime.execution import ExecutionManager, ExecutionRequest

from test_execution_runtime import make_git_project


def py_cmd(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def test_workspace_mutations_are_isolated_and_diffed(tmp_path: Path):
    async def scenario():
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root)
        result = await manager.run(
            ExecutionRequest(
                command=py_cmd(
                    "from pathlib import Path; "
                    "Path('tracked.txt').write_text('changed\\n'); "
                    "Path('new.txt').write_text('new\\n')"
                ),
                timeout=10,
            )
        )

        assert result.success is True
        assert (root / "tracked.txt").read_text(encoding="utf-8") == "original\n"
        assert not (root / "new.txt").exists()
        assert result.workspace_diff is not None
        assert "tracked.txt" in result.workspace_diff.diff_text
        assert "new.txt" in result.workspace_diff.untracked_files

    asyncio.run(scenario())


def test_workspace_cleanup_removes_execution_root(tmp_path: Path):
    async def scenario():
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root)
        result = await manager.run(ExecutionRequest(command=py_cmd("print('clean')"), timeout=10))

        manifest_root = Path(result.manifest_path).parent
        execution_root = Path(
            __import__("json").loads((manifest_root / "manifest.json").read_text(encoding="utf-8"))["workspace"]["execution_root"]
        )
        assert not execution_root.exists()

    asyncio.run(scenario())
