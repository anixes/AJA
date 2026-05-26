# Canonical Execution Model

> **V1 Certified** — Execution is now replay-authoritative via `ActivityContext` + append-only event journal.

Runtime command execution is owned by `aja.runtime.execution.ExecutionManager`. Orchestration, scheduler, CLI, TUI, and mobile clients may request execution, but they do not own subprocess semantics.

All processes and I/O pipelines are abstracted under a **Canonical Asynchronous Execution Transport Runtime** that separates lifecycle ownership from direct transport streams.

## Structural Abstractions

```
ExecutionSession (State/Lifecycle)
       ↓
ExecutionTransport (Unified I/O Transport Interface)
 ├── PipeTransport (Standard IO pipe adapter)
 └── PTYTransport (Event-loop-native pseudo-terminal)
       ├── POSIXPTYTransport (pty.openpty/add_reader)
       └── WindowsPTYTransport (cooperative ConPTY)
```

1. **`ExecutionSession`**: Owns lifecycle states, session paths, and final outputs.
2. **`ExecutionTransport`**: Standardizes the I/O interface (`stdin`, `stdout`, `stderr`, `pid`, `wait`, `terminate`).
3. **`PipeTransport`**: Wraps traditional non-PTY process orchestration cleanly.
4. **`PTYTransport`**: Enforces event-loop-native PTY streams. Uses zero background threads on Unix systems (via `loop.add_reader`) and integrates cooperative cancellation token cleanups on Windows.

---

## State Machine Validation & Lifecycle FSM

The execution session enforces rigid transition rules on its lifecycle state to ensure system consistency and prevent runtime state corruption.

### State Transition Graph
The state flow follows strict, predefined pathways under the `ALLOWED_TRANSITIONS` matrix:
* **`created`** → `starting`
* **`starting`** → `running`, `failed`, `cancelled`
* **`running`** → `graceful_shutdown`, `completed`, `failed`, `cancelled`, `timeout`
* **`graceful_shutdown`** → `force_kill`, `cancelled`, `timeout`
* **`force_kill`** → `cancelled`, `timeout`

### FSM Safety Guarantees
* **Validation Guard**: Any attempt to perform an invalid state transition immediately halts execution and raises a `StateTransitionError`.
* **Crash & Race Consistency**: During concurrent operator cancellation or execution timeouts, the `ExecutionManager` respects the terminal session state (e.g. `"cancelled"`, `"timeout"`, `"failed"`) in the execution driver. If the session has already reached a terminal state via an out-of-band management trigger, the FSM finalization adapts to this terminal state, eliminating invalid transition crashes.

---

## Durable Activity Execution (Replay Model)

Every logical step in a mission is wrapped by `ActivityContext` — a `ContextVar`-based manager that provides Temporal-style durable execution without an external workflow engine.

```
Step Execution Request
        │
        ▼
 ActivityContext.enter(step_id)
        │
        ├── LIVE MODE ──────────► execute step ──► journal.append(result) ──► return result
        │
        └── REPLAY MODE ────────► journal.read(step_id) ──────────────────────► return result
                                         │
                                         └── divergence? ──► raise ReplayDivergenceError
```

**Key properties:**
- Steps are **never executed twice** during replay — the journal result is authoritative.
- `ReplayDivergenceError` is raised if live output diverges from the journaled result — the system fails fast.
- Context is propagated as a `ContextVar`; each async task inherits its own isolated context.

---

## Lifecycle Steps

1. A caller creates an `ExecutionRequest`.
2. `ExecutionManager.start()` creates an `ExecutionSession` with a trace-correlated session ID.
3. `WorkspaceManager` prepares an isolated execution root.
4. `ActivityContext` wraps the execution step; if replaying, returns journaled result immediately.
5. `ExecutionTransport.create(request)` dynamically instantiates the correct Pipe or PTY transport, spawning the process and mapping PID metrics.
6. Stdout and stderr are processed by the `StreamNormalizer` and sequenced by the `EventSequencer`.
7. Stream chunks are written sequentially as a durable, append-only journal by `TelemetryEmitter`.
8. Timeout or cancellation triggers graceful process-tree termination, followed by forced cleanup.
9. A workspace diff and artifact inventory are captured.
10. The isolated workspace is cleaned up.
11. `ExecutionResult`, `manifest.json`, `timeline.jsonl` (deterministic event journal), `stdout.log`, `stderr.log`, and `workspace_diff.json` remain under `.aja/executions/<session_id>`.

## Compatibility

The old sandbox and terminal capability APIs still exist, but they delegate to the canonical manager. Their return shapes remain compatibility-preserving.

## Operator Flow

Operators can inspect execution history with:

```powershell
aja exec list
aja exec show <session_id>
aja exec timeline <session_id>
aja exec diff <session_id>
aja exec cleanup
```
