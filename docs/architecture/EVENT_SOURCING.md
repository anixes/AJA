# Event-Sourced Architecture

This document describes AJA's event-sourcing model introduced in the V1 release. It covers the append-only journal, the `MissionReducer`, projection management, `ActivityContext` durable execution, and schema versioning.

---

## 1. Core Principle

**The `.jsonl` event journal is the single source of truth.**

LanceDB tables (`aja_missions`, `aja_tasks`) are strictly **read-projections** — derived views rebuilt deterministically from journal events. They are never authoritative. If a projection is corrupted or out of sync, `aja rebuild-projections` will reconstruct it correctly from the journal.

This is the same principle used by Apache Kafka, Event Store, and Temporal:
> *State is derived. Events are facts.*

---

## 2. Journal Format

Journals are stored as append-only `.jsonl` files under `.aja/journals/`.

Each line is a JSON object with the following required fields:

```json
{
  "event_id": "uuid-v4",
  "schema_version": "v1",
  "event_type": "task_created",
  "aggregate_id": "mission-id or job-id",
  "timestamp": "2026-05-26T09:22:35.123456Z",
  "payload": { ... }
}
```

**Invariants:**
- Events are only ever **appended** — never mutated or deleted.
- `event_id` is globally unique (UUID v4).
- `schema_version` enables forward-compatible replay.
- `aggregate_id` links the event to its owning mission or job.

---

## 3. Event Types

### Mission Events
| Event Type | Description |
|---|---|
| `mission_created` | A new mission has been queued |
| `mission_started` | Execution of the mission has begun |
| `mission_step_started` | A single mission step has begun |
| `mission_step_completed` | A step completed with a result |
| `mission_step_failed` | A step failed with an error |
| `mission_completed` | The mission finished successfully |
| `mission_failed` | The mission terminated with failure |
| `mission_cancelled` | The mission was cancelled by the operator |

### Scheduler Job Events
| Event Type | Description |
|---|---|
| `job_scheduled` | A cron job was registered |
| `job_triggered` | A cron tick fired and started execution |
| `job_completed` | A job tick completed |
| `job_failed` | A job tick failed |
| `job_cancelled` | A job was removed from the scheduler |

---

## 4. MissionReducer (Pure Function)

The `MissionReducer` in `aja/runtime/mission/reducer.py` is a **pure function** that takes a sequence of events and produces a mission state. It has no I/O, no randomness, and no side effects.

```python
def reduce(events: list[JournalEvent]) -> MissionState:
    state = MissionState.empty()
    for event in events:
        state = apply(event, state)
    return state
```

**Guarantee**: Given identical event sequences, the reducer always produces identical state. This is the foundation of the `rebuild-projections` CLI command.

---

## 5. VersionedEventRehydrator

Defined in `aja/runtime/journal/event_schema.py`, the `VersionedEventRehydrator` handles schema migrations so that old journal events are correctly replayed even after schema upgrades.

```python
rehydrator = VersionedEventRehydrator()
state = rehydrator.rehydrate(journal_path)
```

**Schema upgrade workflow:**
1. Add a new schema version (e.g., `v2`) to `EVENT_SCHEMAS`.
2. Add a migration function to `MIGRATIONS["v1"]["v2"]`.
3. `VersionedEventRehydrator` automatically applies migrations during replay.
4. Old journals continue to replay correctly — no data migration needed.

---

## 6. ActivityContext (Durable Execution)

`ActivityContext` in `aja/runtime/execution/activity.py` wraps each execution step to provide crash-safe, idempotent re-execution.

### How it works

```python
async with ActivityContext(step_id="step-001", journal=journal) as ctx:
    result = await ctx.execute(my_async_function, *args)
```

| Mode | Behavior |
|---|---|
| **Live** | Executes `my_async_function`, records result in journal, returns result |
| **Replay** | Reads result from journal at `step_id`, returns it without re-executing |
| **Divergence** | If live result ≠ journal result → raises `ReplayDivergenceError` |

### ContextVar propagation

`ActivityContext` uses Python's `ContextVar` for isolation:
- Each `asyncio.Task` inherits a copy of its parent's context.
- Thread pool workers receive an explicit context copy.
- Test teardown must call `set_activity_context(None)` to reset state between test cases.

---

## 7. Projection Rebuild CLI

```bash
python -m aja rebuild-projections
```

This command:
1. Scans all journal files under `.aja/journals/`.
2. Replays every event through `VersionedEventRehydrator` + `MissionReducer`.
3. Drops and recreates the `aja_missions` and `aja_tasks` LanceDB tables.
4. Validates that the rebuilt state is consistent.

**When to use:**
- After a crash that may have left projections in an inconsistent state.
- After a schema migration.
- When debugging state discrepancies between the journal and LanceDB.
- As a routine health check to verify journal integrity.

---

## 8. Chaos Resilience

The V1 certification chaos test suite (`tests/python/`) verifies:

| Scenario | Expected Behavior |
|---|---|
| Process crash mid-step | Rehydration from journal; step not re-executed |
| Journal write failure | `IOError` propagated; no partial state committed |
| Divergence on replay | `ReplayDivergenceError` raised immediately |
| Schema upgrade (v1→v2) | `VersionedEventRehydrator` migrates transparently |
| Cross-test journal pollution | Pre-test purge of test aggregate IDs |
| Concurrent scheduler ticks | `active_run_id` lock prevents double execution |

All scenarios pass in the 223-test regression suite.

---

## 9. Related Modules

| Module | Role |
|---|---|
| `aja/runtime/journal/` | Journal I/O, append, read |
| `aja/runtime/journal/event_schema.py` | `JournalEvent`, `VersionedEventRehydrator`, `EVENT_SCHEMAS`, `MIGRATIONS` |
| `aja/runtime/mission/reducer.py` | `MissionReducer` — pure function |
| `aja/runtime/execution/activity.py` | `ActivityContext`, `set_activity_context`, `ReplayDivergenceError` |
| `aja/runtime/execution/rehydrator.py` | Session-level rehydration entry point |
| `libs/aja-core/aja/main.py` | `rebuild-projections` CLI command |
| `tests/python/test_phase6_schema_versioning.py` | Schema versioning and projection rebuild tests |
| `tests/python/test_phase5_event_sourced_mission.py` | Event-sourced mission lifecycle tests |
| `tests/python/test_phase3_event_sourcing.py` | Core journal + reducer tests |
