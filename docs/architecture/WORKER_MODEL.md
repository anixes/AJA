# Worker Model

In AJA Runtime, a **Worker** is an isolated entity responsible for taking a concrete step (defined in a Baton) and executing it securely against the host system.

## 1. Classification of Workers

### In-Process Async Workers
- **Location**: `aja/runtime/autonomous_loop.py`
- **Semantics**: Lightweight threads running inside the main AJA process. They share the main `asyncio` event loop.
- **Guarantees**: Fast, minimal IPC overhead.
- **Risks**: Blocking the event loop if third-party libraries (e.g., synchronous HTTP calls or long CPU loops) are used without `asyncio.to_thread`.

### Sandboxed Subprocess Workers
- **Location**: `aja/runtime/sandbox.py`
- **Semantics**: Processes spawned to handle raw bash/shell execution.
- **Guarantees**: Strict resource bounds via Docker (`--memory=256m`, `--cpus=0.5`). 

### Distributed Remote Workers
- **Location**: Interacted via `transmit_baton` in `handover.py`.
- **Semantics**: Workers running on different physical hosts that accept baton payloads via HTTP POST.
- **Maturity**: Highly experimental. Serialization works, but network-partition recovery logic is not yet fully hardened.

## 2. Worker Lifecycle & Heartbeats

To ensure workers do not silently fail or orphan tasks, they participate in a heartbeat protocol.

- A worker publishes a heartbeat via `LanceRuntimeStore.publish_heartbeat(worker_id)`.
- If a worker crashes while holding a task lock (`active_run_id` in LanceDB metadata), the orchestrator can detect the missing heartbeat and eventually orphan the lock, allowing the task to be rescheduled.

## 3. Concurrency Limits
Concurrency is bounded explicitly by the scheduler and worker pool configurations, preventing fork-bomb scenarios where agents spawn infinite sub-agents. Current defaults force serial goal execution within the `autonomous_loop.py` queue.
