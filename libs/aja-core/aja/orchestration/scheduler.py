import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from aja.orchestration.activity_rt import Activity, ActivityResult, ActivityRuntime, ActivityType


@dataclass
class ActivityBatchResult:
    results: List[ActivityResult]
    duration_ms: int
    success: bool
    partial_success: bool = False
    failures: List[ActivityResult] = field(default_factory=list)


class ParallelActivityScheduler:
    def __init__(
        self,
        runtime: ActivityRuntime,
        *,
        concurrency_limit: int = 4,
        fail_fast: bool = False,
    ):
        self.runtime = runtime
        self.concurrency_limit = max(1, concurrency_limit)
        self.fail_fast = fail_fast
        self._python_sem = asyncio.Semaphore(self.concurrency_limit)
        self._shell_lock = asyncio.Lock()
        self._desktop_lock = asyncio.Lock()
        self._browser_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._mcp_locks: Dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(4))

    async def run_batch(self, activities: List[Activity]) -> ActivityBatchResult:
        t0 = time.monotonic()
        if self.fail_fast:
            results = []
            for activity in activities:
                result = await self._run_one(activity)
                results.append(result)
                if not result.success:
                    break
        else:
            results = await asyncio.gather(*(self._run_one(activity) for activity in activities))

        failures = [result for result in results if not result.success]
        success = not failures and len(results) == len(activities)
        partial_success = bool(failures) and any(result.success for result in results)
        return ActivityBatchResult(
            results=list(results),
            duration_ms=int((time.monotonic() - t0) * 1000),
            success=success,
            partial_success=partial_success,
            failures=failures,
        )

    async def _run_one(self, activity: Activity) -> ActivityResult:
        if activity.activity_type == ActivityType.PYTHON:
            async with self._python_sem:
                return await self.runtime.run(activity)
        if activity.activity_type in {ActivityType.SHELL, ActivityType.DOCKER}:
            async with self._shell_lock:
                return await self.runtime.run(activity)
        if activity.activity_type == ActivityType.MCP:
            server_id = activity.metadata.get("server_id", "default")
            async with self._mcp_locks[server_id]:
                return await self.runtime.run(activity)
        if activity.activity_type == ActivityType.BROWSER:
            mission_id = activity.mission_id or activity.trace_id
            async with self._browser_locks[mission_id]:
                return await self.runtime.run(activity)
        if activity.activity_type == ActivityType.DESKTOP:
            async with self._desktop_lock:
                return await self.runtime.run(activity)
        return await self.runtime.run(activity)
