---
name: getting-started
description: Technical onboarding guide to write durable activities.
---
# Getting Started

To write a durable activity in the AJA runtime:
1. Initialize a `MissionJournal` backing the event log.
2. Define a structured `Activity` with correct parameters (args, tool name, retry policy).
3. Execute through the `ActivityRuntime` using `await runtime.run(activity)`.

Example:
```python
from aja.orchestration.activity_rt import ActivityRuntime, Activity, ActivityType, RetryPolicy
from aja.runtime.mission_journal import MissionJournal

journal = MissionJournal("my-task-id")
runtime = ActivityRuntime(journal=journal)
activity = Activity(
    tool="run_shell_command",
    args={"cmd": "echo 'Hello AJA'"},
    activity_type=ActivityType.SHELL,
    trace_id="my-trace"
)
result = await runtime.run(activity)
```
