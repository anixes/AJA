---
name: swarm-coordination
description: How to orchestrate multi-agent swarm missions — planning, worker delegation, baton handover, and monitoring.
---
# Swarm Coordination

## 1. Launch a swarm mission (CLI)

```bash
python -m aja run "Refactor the payment module and add tests" --dry-run   # audit first
python -m aja run "Refactor the payment module and add tests"              # execute live
```

## 2. Goal session (single relentless agent)

Use `GoalSession` for tasks that a single worker can drive to completion autonomously.

```python
from aja.orchestration.goal_session import GoalSession
import anyio

session = GoalSession(max_iterations=8, timeout_seconds=600)
anyio.run(session.run, "Write and test a CSV parser in libs/utils/csv_parser.py")
```

## 3. Swarm session (Planner + Workers + Critic)

Use `GoalSwarmSession` for complex goals requiring HTN planning and adversarial review.

```python
from aja.orchestration.goal_session import GoalSwarmSession
import anyio

swarm = GoalSwarmSession(max_iterations=5)
anyio.run(swarm.run, "Migrate the LanceDB schema and rebuild all projections")
```

## 4. Baton handover — suspend and resume across hosts

```python
from aja.runtime.handover import BatonManager

bm = BatonManager()
# Suspend
code = bm.capture("Objective text", orchestrator_state_dict)
print("Resume code:", code)          # share this code

# Resume on another process / host
state = bm.pickup(code)
```

Alternatively from the CLI:
```bash
python -m aja pickup <code>
```

## 5. Monitor live execution

```bash
python -m aja tui          # curses dashboard — live HTN DAG + logs + metrics
python -m aja exec list    # list all sessions and their statuses
```

## Model roles
| Role    | Env var               | Responsibility                        |
|---------|-----------------------|---------------------------------------|
| Planner | `AJA_PLANNER_MODEL`   | HTN plan generation, goal decomposition |
| Worker  | `AJA_WORKER_MODEL`    | Atomic task execution                 |
| Critic  | `AJA_CRITIC_MODEL`    | Adversarial review of worker output   |

Set these in `.env` or export before launching.

## Rules
- Always dry-run first for destructive goals.
- The critic model should differ from the worker — opposing system prompts give higher quality output.
- Check `python -m aja doctor` before any swarm launch to confirm LanceDB and native modules are healthy.
