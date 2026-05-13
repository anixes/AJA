# Phase 7: Resilient Recovery Layer

The **Resilient Recovery Layer** transforms Agent from an ephemeral execution script into a robust, state-aware agent platform capable of surviving process crashes, network failures, and non-deterministic tool behavior.

## Core Objective
Ensure that no mission is lost to a crash and no side-effect (e.g., payments, emails) is duplicated during a retry. Move from "fire-and-forget" execution to a **Controller-driven** architecture where the database is the source of truth.

## Key Components

### 1. Authoritative Task Layer (`tasks.py`)
Agent now tracks the full lifecycle of every execution mission in LanceDB/Arrow.
- **Statuses**: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `FAILED_PERMANENT`, `INTERRUPTED`, `SKIPPED_DUPLICATE`.
- **Deduplication**: Uses `logical_task_id` to detect and coalesce duplicate missions, even if rephrased or retried.
- **Run Identity**: Every mission is tagged with a unique `run_id` (UUID) to track execution lineage.

### 2. Recovery Engine (`recovery.py`)
An automated boot-time process that:
- Detects tasks left in `RUNNING` state (indicating a crash).
- Transitions them to `INTERRUPTED`.
- Re-queues them for execution if they haven't exceeded `MAX_RETRIES`.
- Cleans up stale task locks.

### 3. ToolGuard: Atomic Tool Idempotency (`tools.py`)
The most critical hardening layer. `ToolGuard` wraps tool calls to ensure safety.
- **Atomic Reservation**: Uses `INSERT OR IGNORE` in `BEGIN IMMEDIATE` transactions to prevent two agents from running the same tool call simultaneously.
- **Result Caching (Coalescing)**: If a tool was already completed successfully for a given task, `ToolGuard` returns the cached result instead of re-running the side-effect.
- **Failure Classification**: Differentiates between `RETRYABLE` (network/transient) and `PERMANENT` (logic/auth) errors.

### 4. Task-Level Locking
Prevents parallel execution collisions. Before a `logical_task_id` starts, it must acquire an atomic lock in the `task_locks` table. If another process is already working on it, the second process safely backs off.

### 5. Automatic TTL Maintenance
To prevent the LanceDB/Arrow database from growing indefinitely, Agent runs a silent cleanup on startup:
- Prunes `tasks` older than 30 days.
- Prunes `tool_executions` older than 30 days.

## Interfaces

### CLI Integration
- **Recovery**: Runs automatically on every `agent` command invocation.
- **Tracking**: `agent status` now reflects authoritative DB state rather than just active process lists.

### FastAPI / API Bridge
- **Persistence**: New endpoints for querying task history and tool execution logs.
- **Resilience**: The `/swarm/run` endpoint now returns the `logical_task_id` and `run_id` for client-side tracking.

## Architecture Insight
> **LLM** = Unreliable Planner  
> **Runtime** = Reliable Controller  
> **DB** = Source of Truth  

## Validation & Invariants

The system has been empirically validated against the following 7 critical invariants:

| Invariant | Guarantee | Status |
| :--- | :--- | :--- |
| **1. At-Most-Once Task** | A `logical_task_id` executes side-effects at most once. | ✅ **VERIFIED** |
| **2. Tool Idempotency** | A tool `idempotency_key` never executes more than once. | ✅ **VERIFIED** |
| **3. State Integrity** | A task in `COMPLETED` never reverts to `RUNNING`. | ✅ **VERIFIED** |
| **4. Lock Safety** | Locks prevent parallel collisions and are released on finish. | ✅ **VERIFIED** |
| **5. Retry Ceiling** | `retry_count` never exceeds `MAX_RETRIES`. | ✅ **VERIFIED** |
| **6. Recovery Integrity** | Recovery resumes crashed tasks without creating duplicates. | ✅ **VERIFIED** |
| **7. Result Coalescing** | Duplicate requests return cached results instead of re-executing. | ✅ **VERIFIED** |

### Test Harness
A dedicated test suite is available in `tests/system_tests.py`. It uses a simulated side-effect tool (`scripts/test_idempotent_tool.py`) to verify behavior under crashes, concurrency, and failures.

To run the validation:
```bash
$env:AI_PROVIDER="openai"; $env:AI_KEY="dummy"; python tests/system_tests.py
```
