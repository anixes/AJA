import asyncio

from aja.orchestration.activity_rt import Activity, ActivityResult, ActivityType
from aja.orchestration.scheduler import ParallelActivityScheduler


class TrackingRuntime:
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def run(self, activity):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return ActivityResult(tool=activity.tool, success=True, data=activity.tool)


def activity(name, activity_type):
    return Activity(tool=name, args={}, activity_type=activity_type, trace_id="tr-scheduler")


def test_scheduler_runs_python_activities_in_parallel():
    async def run():
        runtime = TrackingRuntime()
        scheduler = ParallelActivityScheduler(runtime, concurrency_limit=3)
        batch = await scheduler.run_batch([
            activity("a", ActivityType.PYTHON),
            activity("b", ActivityType.PYTHON),
            activity("c", ActivityType.PYTHON),
        ])
        return runtime, batch

    runtime, batch = asyncio.run(run())

    assert batch.success is True
    assert len(batch.results) == 3
    assert runtime.max_active > 1


def test_scheduler_serializes_shell_activities():
    async def run():
        runtime = TrackingRuntime()
        scheduler = ParallelActivityScheduler(runtime, concurrency_limit=3)
        batch = await scheduler.run_batch([
            activity("a", ActivityType.SHELL),
            activity("b", ActivityType.SHELL),
            activity("c", ActivityType.SHELL),
        ])
        return runtime, batch

    runtime, batch = asyncio.run(run())

    assert batch.success is True
    assert len(batch.results) == 3
    assert runtime.max_active == 1
