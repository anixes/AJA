"""
Unit Tests: WorkspaceMemoryPool
"""

import pytest
import shutil
from pathlib import Path

from aja.workspace.context import WorkspaceContext, set_current_workspace, reset_current_workspace
from aja.memory.workspace_pool import WorkspaceMemoryPool


@pytest.fixture
def temp_pool_dir(tmp_path):
    d = tmp_path / "pool_storage"
    d.mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_memory_pool_per_workspace_isolation(temp_pool_dir):
    pool = WorkspaceMemoryPool(max_connections=4, ttl_seconds=60)

    dir_a = temp_pool_dir / "ws_a"
    dir_b = temp_pool_dir / "ws_b"

    mgr_a = pool.get_manager("ws_a", memory_dir=dir_a)
    mgr_b = pool.get_manager("ws_b", memory_dir=dir_b)

    assert mgr_a is not mgr_b
    assert Path(mgr_a.db_path).resolve() == (dir_a / "lancedb").resolve()
    assert Path(mgr_b.db_path).resolve() == (dir_b / "lancedb").resolve()

    # Verify cached retrieval
    mgr_a_cached = pool.get_manager("ws_a", memory_dir=dir_a)
    assert mgr_a_cached is mgr_a

    pool.close_all()


def test_memory_pool_contextvar_integration(temp_pool_dir):
    pool = WorkspaceMemoryPool(max_connections=4, ttl_seconds=60)

    ws_path = temp_pool_dir / "proj_x"
    ws_path.mkdir()
    storage = temp_pool_dir / "storage_x"

    ctx = WorkspaceContext(id="ws-x", name="proj-x", path=ws_path, storage_dir=storage)
    token = set_current_workspace(ctx)

    try:
        mgr = pool.get_manager()
        assert Path(mgr.db_path).resolve() == (storage / "memory" / "lancedb").resolve()
    finally:
        reset_current_workspace(token)
        pool.close_all()
