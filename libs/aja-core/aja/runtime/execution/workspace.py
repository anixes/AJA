from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from aja.config import PROJECT_ROOT, DATA_DIR
from aja.runtime.execution.contracts import WorkspaceDiff, WorkspaceSnapshot


class WorkspaceManager:
    """Creates isolated local execution roots and summarizes mutations."""

    def __init__(self, project_root: Optional[Path] = None, base_dir: Optional[Path] = None):
        self.project_root = (project_root or PROJECT_ROOT).resolve()
        self.base_dir = (base_dir or DATA_DIR / "workspaces").resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create(self, session_id: str, mode: str = "isolated") -> WorkspaceSnapshot:
        artifact_root = DATA_DIR / "executions" / session_id / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)

        if mode == "direct":
            return WorkspaceSnapshot(
                session_id=session_id,
                source_root=str(self.project_root),
                execution_root=str(self.project_root),
                artifact_root=str(artifact_root),
                mode="direct",
                cleanup_required=False,
            )

        execution_root = self.base_dir / session_id
        if execution_root.exists():
            shutil.rmtree(execution_root, ignore_errors=True)

        if self._can_use_worktree():
            created = self._create_worktree(execution_root)
            if created:
                return WorkspaceSnapshot(
                    session_id=session_id,
                    source_root=str(self.project_root),
                    execution_root=str(execution_root),
                    artifact_root=str(artifact_root),
                    mode="git_worktree",
                )

        self._copy_workspace(execution_root)
        return WorkspaceSnapshot(
            session_id=session_id,
            source_root=str(self.project_root),
            execution_root=str(execution_root),
            artifact_root=str(artifact_root),
            mode="temp_copy",
        )

    def diff(self, snapshot: WorkspaceSnapshot) -> WorkspaceDiff:
        root = Path(snapshot.execution_root)
        diff_text = ""
        untracked: List[str] = []
        if snapshot.mode in {"git_worktree", "temp_copy"} and (root / ".git").exists():
            untracked_text = self._run_git(root, ["ls-files", "--others", "--exclude-standard"])
            untracked = [line for line in untracked_text.splitlines() if line.strip()]
            self._run_git(root, ["add", "-A"])
            diff_text = self._run_git(root, ["diff", "--staged", "--binary"])

        artifact_files = self._list_relative_files(Path(snapshot.artifact_root))
        return WorkspaceDiff(
            session_id=snapshot.session_id,
            diff_text=diff_text,
            untracked_files=untracked,
            artifact_files=artifact_files,
        )

    def prepare_merge_summary(self, snapshot: WorkspaceSnapshot) -> dict:
        workspace_diff = self.diff(snapshot)
        return {
            "session_id": snapshot.session_id,
            "execution_root": snapshot.execution_root,
            "source_root": snapshot.source_root,
            "changed": bool(workspace_diff.diff_text or workspace_diff.untracked_files),
            "untracked_files": workspace_diff.untracked_files,
            "artifact_files": workspace_diff.artifact_files,
            "diff": workspace_diff.diff_text,
        }

    def cleanup(self, snapshot: WorkspaceSnapshot) -> None:
        if not snapshot.cleanup_required:
            return
        root = Path(snapshot.execution_root)
        if snapshot.mode == "git_worktree":
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(root)],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=20,
            )
        if root.exists():
            import time
            for _ in range(5):
                try:
                    shutil.rmtree(root, ignore_errors=False)
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                shutil.rmtree(root, ignore_errors=True)

    def cleanup_stale(self) -> List[str]:
        removed: List[str] = []
        if not self.base_dir.exists():
            return removed
        for path in self.base_dir.iterdir():
            if not path.is_dir():
                continue
            try:
                shutil.rmtree(path)
                removed.append(path.name)
            except Exception:
                continue
        return removed

    def _can_use_worktree(self) -> bool:
        if not (self.project_root / ".git").exists():
            return False
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False

    def _create_worktree(self, execution_root: Path) -> bool:
        try:
            res = subprocess.run(
                ["git", "worktree", "add", "--detach", str(execution_root), "HEAD"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _copy_workspace(self, execution_root: Path) -> None:
        ignore = shutil.ignore_patterns(
            ".git",
            ".aja",
            ".pytest_cache",
            ".pytest-aja",
            "node_modules",
            "dist",
            "__pycache__",
        )
        shutil.copytree(self.project_root, execution_root, ignore=ignore)

    def _run_git(self, cwd: Path, args: List[str]) -> str:
        try:
            res = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=20,
            )
            return res.stdout if res.returncode == 0 else ""
        except Exception:
            return ""

    def _list_relative_files(self, root: Path) -> List[str]:
        if not root.exists():
            return []
        return [str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()]
