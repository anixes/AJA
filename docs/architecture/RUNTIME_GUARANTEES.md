# Runtime Guarantees

As AJA evolves into a production-grade infrastructure platform, the core engine must provide explicit behavioral guarantees. This document defines the formal semantics that contributors and clients can rely on.

## 1. Scheduler Guarantees (`cron_scheduler.py`)
- **Persistence First**: No job is executed until its metadata is safely committed to the `RuntimeTaskStore`.
- **Interrupt Limits**: Any scheduled job running under `SwarmEngine.execute_direct` or `plan_and_execute_batons` is guaranteed to be violently interrupted if it exceeds a hard 3-minute execution limit. 
- **Concurrency Locks**: A scheduled job sets an `active_run_id` lock in LanceDB. A job will never be triggered concurrently if the previous tick is still holding the lock.

## 2. Baton Guarantees (`handover.py`)
- **Zero-Copy Integrity**: The Arrow IPC buffer loaded into `_IN_MEMORY_BATONS` is immutable. It guarantees that picking up a baton locally incurs exactly O(1) serialization overhead.
- **Context Preservation**: A generated Baton is guaranteed to contain the `trace_id` of the task that spawned it, ensuring lineage survives network transmission.

## 3. Persistence Guarantees (`lance_stores.py`)
- **Schema Validation**: Any data written to the Task Store or Event Sink is guaranteed to be validated against the Pydantic schemas defined in `config_schema.py`. Malformed configurations trigger immediate defaults or rejections rather than corrupting the database.

## 4. Trace Guarantees (`telemetry.py`)
- **Async Context Continuity**: An `asyncio.create_task` spawned within a `TraceContextManager` block is guaranteed to inherit the parent `trace_id`.
- **Auditability**: Any command passed to the execution sandbox is guaranteed to generate a `security_audit` event, regardless of whether the decision was to allow or deny.

## 5. Failure Semantics
- **Crash Recovery**: If the Python process dies abruptly, any task in the `in_progress` state without a corresponding recent heartbeat will eventually be orphaned and marked available for retry by the scheduler on reboot.
- **Rollback Limitation**: AJA does not automatically merge execution changes into the source workspace. Isolated execution roots are cleaned up after a diff and artifact inventory are captured. Applying those changes remains an explicit operator-approved action.

## 6. Execution Runtime Guarantees
- **Canonical Ownership**: Runtime command execution is owned by `ExecutionManager`.
- **Timeout Semantics**: A timeout records `state=timeout`, emits attribution telemetry, and attempts graceful then forced process-tree cleanup.
- **Cancellation Semantics**: Cancellation is idempotent. Multiple cancellation requests settle to one final result.
- **Workspace Semantics**: Commands run in an isolated worktree or temp copy by default. The live repository is not mounted read-write into Docker.
- **Telemetry Semantics**: Stdout/stderr stream lines, lifecycle transitions, workspace diffs, and cleanup outcomes are emitted with trace correlation.
- **Replay Semantics**: AJA records manifests, timelines, stream logs, result files, process metadata, and workspace diffs. It does not claim deterministic replay.
