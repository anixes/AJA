"""
Unit Tests: KernelScheduler & Priority Mission Queue
"""

import asyncio
import pytest
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aja.kernel.scheduler import (
    KernelScheduler,
    MissionStatus,
    PriorityLevel,
    PriorityItem,
)
from aja.workspace.manager import WorkspaceRegistry, Workspace


@pytest.fixture
def temp_kernel_env(tmp_path):
    storage = tmp_path / "kernel_data"
    storage.mkdir()
    
    ws1_dir = tmp_path / "repo1"
    ws1_dir.mkdir()
    ws2_dir = tmp_path / "repo2"
    ws2_dir.mkdir()

    reg = WorkspaceRegistry(storage_root=storage)
    reg.add(ws1_dir, name="backend")
    reg.add(ws2_dir, name="frontend")

    yield storage, reg
    shutil.rmtree(storage, ignore_errors=True)


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_scheduler_priority_queue_ordering():
    scheduler = KernelScheduler(max_concurrency=1)

    # Put in reverse priority order
    item_bg = PriorityItem(priority=int(PriorityLevel.BACKGROUND), created_at=100.0, mission_id="m-bg")
    item_norm = PriorityItem(priority=int(PriorityLevel.NORMAL), created_at=101.0, mission_id="m-norm")
    item_urgent = PriorityItem(priority=int(PriorityLevel.URGENT), created_at=102.0, mission_id="m-urgent")

    await scheduler.queue.put(item_bg)
    await scheduler.queue.put(item_norm)
    await scheduler.queue.put(item_urgent)

    # Must pop in order of: URGENT (1), NORMAL (2), BACKGROUND (3)
    p1 = await scheduler.queue.get()
    p2 = await scheduler.queue.get()
    p3 = await scheduler.queue.get()

    assert p1.mission_id == "m-urgent"
    assert p2.mission_id == "m-norm"
    assert p3.mission_id == "m-bg"


@pytest.mark.anyio
async def test_scheduler_submission_and_execution(temp_kernel_env):
    storage, reg = temp_kernel_env

    scheduler = KernelScheduler(max_concurrency=2)
    
    events = []
    def callback(status, payload):
        events.append((status, payload))

    with patch("aja.kernel.scheduler.get_workspace_registry", return_value=reg), \
         patch("aja.kernel.scheduler.GoalSession") as mock_session_cls:
        
        mock_instance = AsyncMock()
        mock_instance.run.return_value = {"completed": True}
        mock_session_cls.return_value = mock_instance

        await scheduler.start()

        req = await scheduler.submit(
            objective="Build microservice API",
            workspace_id="backend",
            priority=PriorityLevel.URGENT,
            source="telegram",
            progress_callback=callback,
        )

        assert req.status in (MissionStatus.QUEUED, MissionStatus.RUNNING)

        # Wait for worker to finish
        for _ in range(20):
            if req.status == MissionStatus.COMPLETED:
                break
            await asyncio.sleep(0.05)

        assert req.status == MissionStatus.COMPLETED
        assert req.result is not None
        assert req.completed_at is not None

        await scheduler.stop()

    assert any(e[0] == "queued" for e in events)
    assert any(e[0] == "started" for e in events)
    assert any(e[0] == "completed" for e in events)


@pytest.mark.anyio
async def test_scheduler_cancellation(temp_kernel_env):
    storage, reg = temp_kernel_env
    scheduler = KernelScheduler(max_concurrency=1)

    with patch("aja.kernel.scheduler.get_workspace_registry", return_value=reg):
        req = await scheduler.submit(
            objective="Long running mission",
            workspace_id="backend",
            priority=PriorityLevel.NORMAL,
        )
        assert req.status == MissionStatus.QUEUED

        # Cancel while still queued
        cancelled = await scheduler.cancel(req.id)
        assert cancelled is True
        assert req.status == MissionStatus.CANCELLED
