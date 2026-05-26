# Runtime Guarantees

As AJA evolves into a production-grade infrastructure platform, the core engine must provide explicit behavioral guarantees. This document defines the formal semantics that contributors and clients can rely on.

> **V1 Certified** — All guarantees below are verified by the 223-test regression suite (0 failures).

---

## 1. Event Journal Guarantees (`aja/runtime/journal/`)

- **Append-Only**: Journal files (`.jsonl`) are never mutated. Events are only ever appended.
- **Schema Versioned**: Every event carries a `schema_version` field. `VersionedEventRehydrator` ensures forward-compatible replay across schema upgrades.
- **Atomic Writes**: Journal appends use atomic file operations. A crash mid-append will not corrupt existing events.
- **Uniqueness**: Each event carries a unique `event_id` (UUID). Duplicate detection is performed on replay.

---

## 2. Projection Guarantees (`aja/runtime/mission/reducer.py`)

- **Derived State Only**: LanceDB tables (`aja_missions`, `aja_tasks`) are read-projections only. They are never authoritative.
- **Deterministic Rebuild**: `aja rebuild-projections` replays the full event journal through the pure `MissionReducer` and produces identical state to the live system. This is verified by `test_phase6_schema_versioning.py`.
- **Pure Reducer**: The `MissionReducer` is a pure function. Given identical event sequences, it always produces identical state. No I/O, no randomness, no side effects.

---

## 3. Durable Execution Guarantees (`aja/runtime/execution/activity.py`)

- **Idempotent Replay**: `ActivityContext` wraps each execution step. On re-entry (crash recovery or explicit replay), the stored journal result is returned without re-executing the step. Steps are never executed twice.
- **Divergence Detection**: If a live step result diverges from the journaled result, `ReplayDivergenceError` is raised immediately. The runtime fails fast rather than silently accumulating state drift.
- **Context Isolation**: `ActivityContext` is propagated via `ContextVar`. Each async task and thread carries its own context. `set_activity_context(None)` resets context safely in test teardown.

---

## 4. Scheduler Guarantees (`cron_scheduler.py`)

- **Persistence First**: No job is executed until its metadata is safely committed to the journal and `RuntimeTaskStore`.
- **Journal-Rehydrated State**: Scheduler job states are always rehydrated from the event journal on startup — never from the projection alone.
- **Interrupt Limits**: Any scheduled job running under `SwarmEngine.execute_direct` or `plan_and_execute_batons` is guaranteed to be interrupted if it exceeds a hard 3-minute execution limit.
- **Concurrency Locks**: A scheduled job sets an `active_run_id` lock in LanceDB. A job will never be triggered concurrently if the previous tick is still holding the lock.

---

## 5. Baton Guarantees (`handover.py`)

- **Zero-Copy Integrity**: The Arrow IPC buffer loaded into `_IN_MEMORY_BATONS` is immutable. Picking up a baton locally incurs exactly O(1) serialization overhead.
- **Context Preservation**: A generated baton is guaranteed to contain the `trace_id` of the task that spawned it, ensuring lineage survives network transmission.
- **Disk Fallback Durability**: In addition to the in-memory cache, batons are written to disk under `libs/aja-core/temp_batons/`. A process crash does not lose the baton.

---

## 6. Trace Guarantees (`telemetry.py`)

- **Async Context Continuity**: An `asyncio.create_task` spawned within a `TraceContextManager` block is guaranteed to inherit the parent `trace_id`.
- **Thread Isolation**: Thread-local context ensures trace IDs do not bleed across concurrent thread pool workers.
- **Auditability**: Any command passed to the execution sandbox generates a `security_audit` event, regardless of allow/deny outcome.

---

## 7. Execution Runtime Guarantees (`aja/runtime/execution/manager.py`)

- **Canonical Ownership**: Runtime command execution is owned exclusively by `ExecutionManager`. No other subsystem spawns raw subprocesses.
- **Timeout Semantics**: A timeout records `state=timeout`, emits attribution telemetry, and attempts graceful then forced process-tree cleanup.
- **Cancellation Semantics**: Cancellation is idempotent. Multiple cancellation requests settle to one final terminal state.
- **Workspace Semantics**: Commands run in an isolated worktree or temp copy by default. The live repository is not mounted read-write into Docker.
- **Telemetry Semantics**: Stdout/stderr lines, lifecycle transitions, workspace diffs, and cleanup outcomes are emitted with trace correlation.
- **Replay Semantics**: AJA records manifests, timelines (`timeline.jsonl`), stream logs, result files, process metadata, and workspace diffs under `.aja/executions/<session_id>/`. Replay is deterministic when driven through `ActivityContext` + journal rehydration.

---

## 8. Resource Governance & Enforced Constraints

- **Policy Boundaries**: Any subprocess execution via `ExecutionManager` is clamped against the global `ExecutionPolicy` (max timeout, memory limits, CPU quotas, network constraints).
- **Audit Trails**: The exact applied resource bounds are recorded under `applied_limits` in the final `ExecutionManifest`.
- **Graceful OS Adaptation**: Limits degrade gracefully to timeout-only controls under raw Windows host execution. Linux/macOS enforce strict virtual memory limits using native POSIX RLIMITs.

---

## 9. Failure Semantics

- **Crash Recovery**: If the Python process dies abruptly, any task in `in_progress` state without a recent heartbeat will be orphaned. On reboot, `aja rebuild-projections` or the scheduler startup will rehydrate correct state from the journal and mark the task available for retry.
- **Rollback Limitation**: AJA does not automatically merge execution changes into the source workspace. Isolated execution roots are cleaned up after diff and artifact capture. Applying changes remains an explicit operator-approved action.
- **No Silent State Corruption**: The combination of journal append-only semantics and `ReplayDivergenceError` ensures the system never silently accumulates incorrect state.
