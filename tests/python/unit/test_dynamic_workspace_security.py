"""
Unit Tests: Dynamic Multi-Workspace Security & Isolation
"""

import os
import pytest
import shutil
import sys
from pathlib import Path

from aja.security.command_guard import classify_command
from aja.workspace.context import (
    WorkspaceContext,
    set_current_workspace,
    reset_current_workspace,
)
from aja.orchestration.tools.executor import ToolExecutor


@pytest.fixture
def isolated_workspaces(tmp_path):
    ws_a = tmp_path / "workspace_alpha"
    ws_a.mkdir()
    ws_b = tmp_path / "workspace_beta"
    ws_b.mkdir()

    # Create dummy files
    (ws_a / "alpha.py").write_text("print('alpha')")
    (ws_b / "beta.py").write_text("print('beta')")

    storage = tmp_path / "storage"
    storage.mkdir()

    ctx_a = WorkspaceContext(
        id="ws-a",
        name="alpha",
        path=ws_a,
        storage_dir=storage / "a",
        config_overrides={"allow_out_of_bounds_paths": False},
    )
    ctx_b = WorkspaceContext(
        id="ws-b",
        name="beta",
        path=ws_b,
        storage_dir=storage / "b",
        config_overrides={"allow_out_of_bounds_paths": False},
    )

    yield ws_a, ws_b, ctx_a, ctx_b
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_command_guard_dynamic_workspace_boundary(isolated_workspaces):
    ws_a, ws_b, ctx_a, ctx_b = isolated_workspaces

    # 1. Bind to Workspace A
    token_a = set_current_workspace(ctx_a)
    try:
        # Command accessing file inside Workspace A
        cmd_a = f'cmd /c type "{ws_a / "alpha.py"}"'
        res_a = classify_command(cmd_a)
        assert res_a["decision"] == "allow", f"Failed on valid Workspace A path: {res_a}"

        # Command accessing file inside Workspace B (Out of bounds for A)
        cmd_cross = f'cmd /c type "{ws_b / "beta.py"}"'
        res_cross = classify_command(cmd_cross)
        assert res_cross["decision"] == "ask", "Cross-workspace access should require confirmation"
        assert any("outside the workspace root" in r for r in res_cross["reasons"])
    finally:
        reset_current_workspace(token_a)

    # 2. Bind to Workspace B
    token_b = set_current_workspace(ctx_b)
    try:
        # Now Workspace B path is allowed
        cmd_b = f'cmd /c type "{ws_b / "beta.py"}"'
        res_b = classify_command(cmd_b)
        assert res_b["decision"] == "allow", f"Failed on valid Workspace B path: {res_b}"

        # And Workspace A is now out of bounds
        cmd_cross_b = f'cmd /c type "{ws_a / "alpha.py"}"'
        res_cross_b = classify_command(cmd_cross_b)
        assert res_cross_b["decision"] == "ask"
        assert any("outside the workspace root" in r for r in res_cross_b["reasons"])
    finally:
        reset_current_workspace(token_b)



def test_tool_executor_dynamic_cwd_resolution(isolated_workspaces):
    ws_a, _, ctx_a, _ = isolated_workspaces

    executor = ToolExecutor()
    token = set_current_workspace(ctx_a)
    try:
        # Run command without explicit cwd
        from unittest.mock import patch, MagicMock
        with patch("aja.security.permissions.PermissionEngine.authorize") as mock_auth:
            mock_res = MagicMock()
            mock_res.allowed = True
            mock_auth.return_value = mock_res

            cmd = f'"{sys.executable}" -c "import os; print(os.getcwd())"'
            result = executor.execute(cmd)
            assert result["status"] == "success", f"Executor failed: {result}"
            output_dir = Path(result["stdout"].strip()).resolve()
            assert output_dir == ws_a.resolve(), f"Expected {ws_a}, got {output_dir}"
    finally:
        reset_current_workspace(token)

