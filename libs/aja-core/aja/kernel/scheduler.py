"""
AJA Kernel Scheduler & Priority Mission Queue
=============================================
Centralized task supervisor managing multi-workspace goal execution,
worker concurrency pools, priority queuing, and cooperative preemption.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from aja.interface.modern import console
from aja.orchestration.goal_session import GoalSession
from aja.workspace.context import (
    WorkspaceContext,
    set_current_workspace,
    reset_current_workspace,
)
from aja.workspace.manager import get_workspace_registry, Workspace


class PriorityLevel(IntEnum):
    URGENT = 1      # On-call telegram emergencies, user interrupts
    NORMAL = 2      # Standard interactive CLI / Telegram missions
    BACKGROUND = 3  # Scheduled cron audits, repo janitoring


class MissionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass(order=True)
class PriorityItem:
    priority: int
    created_at: float
    mission_id: str = field(compare=False)


@dataclass
class MissionRequest:
    id: str
    workspace_id: str
    workspace_name: str
    objective: str
    priority: PriorityLevel = PriorityLevel.NORMAL
    source: str = "cli"
    status: MissionStatus = MissionStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    cancellation_requested: bool = False
    progress_callback: Optional[Callable[[str, Any], None]] = field(
        default=None, compare=False
    )


class KernelScheduler:
    """
    Asynchronous multi-workspace task scheduler and worker pool manager.
    """

    def __init__(self, max_concurrency: int = 2):
        self.max_concurrency = max_concurrency
        self.queue: asyncio.PriorityQueue[PriorityItem] = asyncio.PriorityQueue()
        self.missions: Dict[str, MissionRequest] = {}
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the worker pool."""
        if self._running:
            return
        self._running = True
        for i in range(self.max_concurrency):
            worker_task = asyncio.create_task(
                self._worker_loop(i), name=f"aja-worker-{i}"
            )
            self._workers.append(worker_task)

    async def stop(self) -> None:
        """Gracefully stop all workers."""
        self._running = False
        for task in self.active_tasks.values():
            task.cancel()
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()
        self.active_tasks.clear()

    async def submit(
        self,
        objective: str,
        workspace_id: Optional[str] = None,
        priority: PriorityLevel = PriorityLevel.NORMAL,
        source: str = "cli",
        progress_callback: Optional[Callable[[str, Any], None]] = None,
    ) -> MissionRequest:
        """
        Submit a new mission to the priority queue.
        """
        reg = get_workspace_registry()
        ws = reg.get_or_default(workspace_id)
        
        mission_id = f"m-{uuid.uuid4().hex[:8]}"
        req = MissionRequest(
            id=mission_id,
            workspace_id=ws.id,
            workspace_name=ws.name,
            objective=objective,
            priority=priority,
            source=source,
            status=MissionStatus.QUEUED,
            progress_callback=progress_callback,
        )

        async with self._lock:
            self.missions[mission_id] = req

        # Push to priority queue
        item = PriorityItem(
            priority=int(priority),
            created_at=req.created_at,
            mission_id=mission_id,
        )
        await self.queue.put(item)

        if progress_callback:
            try:
                progress_callback("queued", {"mission_id": mission_id, "workspace": ws.name})
            except Exception:
                pass

        return req

    async def cancel(self, mission_id: str) -> bool:
        """Request cooperative cancellation of a queued or running mission."""
        async with self._lock:
            req = self.missions.get(mission_id)
            if not req:
                return False

            if req.status == MissionStatus.QUEUED:
                req.status = MissionStatus.CANCELLED
                return True

            if req.status == MissionStatus.RUNNING:
                req.cancellation_requested = True
                task = self.active_tasks.get(mission_id)
                if task and not task.done():
                    task.cancel()
                req.status = MissionStatus.CANCELLED
                return True

            return False

    def get_mission(self, mission_id: str) -> Optional[MissionRequest]:
        """Fetch mission details by ID."""
        return self.missions.get(mission_id)

    def list_active(self) -> List[MissionRequest]:
        """List currently running and queued missions."""
        return [
            m for m in self.missions.values()
            if m.status in (MissionStatus.QUEUED, MissionStatus.RUNNING)
        ]

    def list_history(self, limit: int = 50) -> List[MissionRequest]:
        """List past missions sorted by submission time."""
        all_missions = sorted(
            self.missions.values(), key=lambda m: m.created_at, reverse=True
        )
        return all_missions[:limit]

    async def _worker_loop(self, worker_idx: int) -> None:
        """Continuous execution loop for a pool worker."""
        while self._running:
            try:
                item = await self.queue.get()
                mission_id = item.mission_id

                async with self._lock:
                    req = self.missions.get(mission_id)
                    if not req or req.status == MissionStatus.CANCELLED:
                        self.queue.task_done()
                        continue

                    req.status = MissionStatus.RUNNING
                    req.started_at = time.time()

                task = asyncio.create_task(
                    self._execute_mission(req), name=f"mission-{mission_id}"
                )
                self.active_tasks[mission_id] = task

                try:
                    await task
                except asyncio.CancelledError:
                    req.status = MissionStatus.CANCELLED
                finally:
                    self.active_tasks.pop(mission_id, None)
                    self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep(0.5)

    async def _execute_mission(self, req: MissionRequest) -> None:
        """Execute a single mission inside its isolated WorkspaceContext."""
        reg = get_workspace_registry()
        ws = reg.get(req.workspace_id) or reg.get_default_workspace()
        ctx = reg.create_context(ws)

        token = set_current_workspace(ctx)
        try:
            if req.progress_callback:
                req.progress_callback("started", {"mission_id": req.id, "workspace": ws.name})

            # Create GoalSession configured for the workspace
            session = GoalSession(
                dry_run=False,
                workspace_dir=str(ws.resolved_path),
            )

            res = await session.run(req.objective)

            req.completed_at = time.time()
            req.result = {"status": "success", "session_result": res}
            req.status = MissionStatus.COMPLETED

            if req.progress_callback:
                req.progress_callback("completed", {
                    "mission_id": req.id,
                    "workspace": ws.name,
                    "result": res,
                })

        except asyncio.CancelledError:
            req.status = MissionStatus.CANCELLED
            req.completed_at = time.time()
            req.error = "Mission execution cancelled"
            if req.progress_callback:
                req.progress_callback("cancelled", {"mission_id": req.id, "workspace": ws.name})
            raise
        except Exception as e:
            req.status = MissionStatus.FAILED
            req.completed_at = time.time()
            req.error = str(e)
            if req.progress_callback:
                req.progress_callback("failed", {
                    "mission_id": req.id,
                    "workspace": ws.name,
                    "error": str(e),
                })
        finally:
            reset_current_workspace(token)


# Global Singleton Scheduler
_global_scheduler: Optional[KernelScheduler] = None


def get_kernel_scheduler() -> KernelScheduler:
    global _global_scheduler
    if _global_scheduler is None:
        _global_scheduler = KernelScheduler()
    return _global_scheduler
