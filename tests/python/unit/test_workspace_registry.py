"""
Unit Tests: AJA Workspace Registry & Context Management
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from aja.workspace.context import (
    WorkspaceContext,
    get_current_workspace,
    set_current_workspace,
    reset_current_workspace,
)
from aja.workspace.manager import Workspace, WorkspaceRegistry


@pytest.fixture
def temp_storage(tmp_path):
    storage = tmp_path / "aja_kernel_storage"
    storage.mkdir()
    yield storage
    shutil.rmtree(storage, ignore_errors=True)


@pytest.fixture
def sample_repos(tmp_path):
    repo_a = tmp_path / "frontend_repo"
    repo_a.mkdir()
    repo_b = tmp_path / "backend_repo"
    repo_b.mkdir()
    return repo_a, repo_b


def test_workspace_context_coroutine_isolation(sample_repos, temp_storage):
    repo_a, repo_b = sample_repos
    
    ctx_a = WorkspaceContext(
        id="ws-a",
        name="frontend",
        path=repo_a,
        storage_dir=temp_storage / "ws-a",
    )
    ctx_b = WorkspaceContext(
        id="ws-b",
        name="backend",
        path=repo_b,
        storage_dir=temp_storage / "ws-b",
    )

    assert get_current_workspace() is None

    token_a = set_current_workspace(ctx_a)
    assert get_current_workspace().name == "frontend"
    assert get_current_workspace().path == repo_a.resolve()

    token_b = set_current_workspace(ctx_b)
    assert get_current_workspace().name == "backend"
    assert get_current_workspace().path == repo_b.resolve()

    reset_current_workspace(token_b)
    assert get_current_workspace().name == "frontend"

    reset_current_workspace(token_a)
    assert get_current_workspace() is None


def test_workspace_registry_lifecycle(sample_repos, temp_storage):
    repo_a, repo_b = sample_repos
    registry = WorkspaceRegistry(storage_root=temp_storage)

    # 1. Add workspace A
    ws_a = registry.add(repo_a, name="my-frontend")
    assert ws_a.name == "my-frontend"
    assert ws_a.resolved_path == repo_a.resolve()
    assert ws_a.active is True  # First workspace is automatically active

    # 2. Add workspace B
    ws_b = registry.add(repo_b, name="my-backend", set_active=True)
    assert ws_b.name == "my-backend"
    assert ws_b.active is True
    
    # Verify A is no longer active
    assert registry.get(ws_a.id).active is False

    # 3. List workspaces
    all_ws = registry.list_all()
    assert len(all_ws) == 2
    assert {w.name for w in all_ws} == {"my-frontend", "my-backend"}

    # 4. Lookup by name (case-insensitive)
    found_a = registry.get("MY-FRONTEND")
    assert found_a is not None
    assert found_a.id == ws_a.id

    # 5. Switch active workspace
    assert registry.set_active("my-frontend") is True
    assert registry.get_active().id == ws_a.id

    # 6. Persistence across new registry instance
    reloaded_registry = WorkspaceRegistry(storage_root=temp_storage)
    assert len(reloaded_registry.list_all()) == 2
    assert reloaded_registry.get_active().name == "my-frontend"

    # 7. Remove workspace
    assert reloaded_registry.remove("my-frontend") is True
    assert len(reloaded_registry.list_all()) == 1
    assert reloaded_registry.get("my-frontend") is None
    # Auto-activated next workspace
    assert reloaded_registry.get_active().name == "my-backend"
