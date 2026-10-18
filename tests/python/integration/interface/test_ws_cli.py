"""
Integration Tests: AJA Workspace CLI (`aja ws`)
"""

import pytest
import shutil
from pathlib import Path
from unittest.mock import patch

from aja.cli.commands.ws_cmd import cmd_ws
from aja.workspace.manager import WorkspaceRegistry, get_workspace_registry


@pytest.fixture
def cli_workspace_env(tmp_path):
    storage = tmp_path / "cli_kernel_storage"
    storage.mkdir()

    repo_dir = tmp_path / "sample_app"
    repo_dir.mkdir()
    (repo_dir / "index.html").write_text("<h1>Hello AJA</h1>")

    reg = WorkspaceRegistry(storage_root=storage)
    yield storage, repo_dir, reg
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_ws_cli_full_lifecycle(cli_workspace_env):
    storage, repo_dir, reg = cli_workspace_env

    with patch("aja.cli.commands.ws_cmd.get_workspace_registry", return_value=reg):
        # 1. List when empty
        cmd_ws(["list"])

        # 2. Add workspace
        cmd_ws(["add", str(repo_dir), "--name", "web-app"])
        ws = reg.get("web-app")
        assert ws is not None
        assert ws.name == "web-app"
        assert ws.resolved_path == repo_dir.resolve()
        assert ws.active is True

        # 3. Add second workspace
        second_dir = repo_dir.parent / "api_app"
        second_dir.mkdir()
        cmd_ws(["add", str(second_dir), "--name", "api-backend"])
        assert len(reg.list_all()) == 2

        # 4. Switch active workspace
        cmd_ws(["use", "web-app"])
        assert reg.get_active().name == "web-app"

        # 5. Show status
        cmd_ws(["status"])

        # 6. Remove workspace
        cmd_ws(["remove", "api-backend"])
        assert len(reg.list_all()) == 1
        assert reg.get("api-backend") is None
