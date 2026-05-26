# AJA Runtime

**A local-first orchestration runtime and execution substrate for autonomous agents.**

> **v1.0 — V1 Certified** · 223 tests passing · All M1–M8 milestones verified · Replay-authoritative event-sourced architecture

---

## 1. What AJA Runtime Is

AJA Runtime is a persistent workflow engine and systems-level orchestration substrate. It provides the core execution infrastructure required to run autonomous agents reliably on local hardware.

Instead of treating agents as transient chat loops, AJA treats agentic workflows as **standard scheduled compute**. It manages state persistence, process isolation, inter-process communication (IPC), deterministic scheduling, and execution telemetry, allowing developers to build robust AI agents without fighting the underlying infrastructure.

### The Fundamental Guarantee

AJA Runtime is now **replay-authoritative**. The `.jsonl` event journal is the single source of truth for all mission and task state. LanceDB tables (`aja_missions`, `aja_tasks`) are strictly derived **read-projections** rebuilt deterministically from journal events. This means:

- State is never silently lost, even after a crash
- Projections can always be rebuilt from scratch with `aja rebuild-projections`
- Any divergence between runtime state and the journal is a fatal, detectable error

---

## 2. What AJA Runtime is NOT

- **Not a chatbot or "Jarvis clone":** AJA is the runtime infrastructure *underneath* the assistant, not the conversational interface itself.
- **Not a prompt-engineering framework:** AJA does not compete with LangChain or LlamaIndex. It focuses on the execution environment, not the LLM chain.

---

## 3. Core Architecture

AJA's architecture enforces a strict separation between the runtime, the event journal, and the presentation layer.

```text
  +-------------------+        +--------------------+
  |  Clients / UIs    |        |  Scheduled Tasks   |
  | (CLI, TUI, HTTP)  |        | (CronScheduler)    |
  +--------+----------+        +---------+----------+
           |                             |
           v                             v
  +-------------------------------------------------+
  |                  AJA Runtime                    |
  |  +-----------------+       +-----------------+  |
  |  |  Orchestration  | ----> | Durable Journal |  |
  |  |  + ActivityCtx  |       | (.jsonl — SoT)  |  |
  |  +-----------------+       +-----------------+  |
  |          |                         |            |
  |          v                         v            |
  |  +-----------------+       +-----------------+  |
  |  | Rust Baton IPC  | ----> | Read Projections|  |
  |  | (Arrow / PyO3)  |       | (LanceDB tables)|  |
  |  +-----------------+       +-----------------+  |
  +-----------+-------------------------------------+
              |
              v
  +-------------------------------------------------+
  |             Execution Workers                   |
  |  [ PTY / Pipe Transport ]   [ Sandbox ]         |
  +-------------------------------------------------+
```

### Major Subsystems

| Subsystem | Module | Responsibility |
|---|---|---|
| **Event Journal** | `aja/runtime/journal/` | Append-only `.jsonl` source of truth |
| **Rehydrator** | `aja/runtime/execution/rehydrator.py` | Replays journals into `ActivityContext` |
| **ActivityContext** | `aja/runtime/execution/activity.py` | `ContextVar`-based durable execution wrapper |
| **Mission Reducer** | `aja/runtime/mission/reducer.py` | Pure-function event→state reducer |
| **Schema Versioning** | `aja/runtime/journal/event_schema.py` | Versioned event types + `VersionedEventRehydrator` |
| **ExecutionManager** | `aja/runtime/execution/manager.py` | Canonical subprocess lifecycle owner |
| **CronScheduler** | `aja/scheduler/cron_scheduler.py` | LanceDB-backed deterministic job scheduling |
| **Baton IPC** | `aja/runtime/handover.py` | Apache Arrow zero-copy state transfer |
| **Trace Telemetry** | `aja/observability/telemetry.py` | Context-variable trace propagation |

---

## 4. Replay-Authoritative Execution Model

### Journal → Projection Pipeline

```
Event Occurs
    │
    ▼
journal.append(event)          ← Always first, atomic write
    │
    ▼
reducer.apply(event, state)    ← Pure function, no side effects
    │
    ▼
projection.upsert(new_state)   ← LanceDB read-projection update
```

The reducer is a **pure function**: given the same sequence of journal events, it always produces identical state. LanceDB tables are projections only — they are never authoritative.

### ActivityContext & Durable Activities

Every execution step is wrapped by `ActivityContext`, a `ContextVar`-based manager that:
- Intercepts live execution under a real context
- Intercepts **replay execution** by returning stored results from the journal
- Raises `ReplayDivergenceError` if a live result diverges from the logged replay result

This provides Temporal-style durable execution semantics without an external workflow engine.

### Schema Versioning

Event schemas are versioned (`v1`, `v2`, …) via `event_schema.py`. The `VersionedEventRehydrator` handles forward-compatibility, allowing old journals to be replayed correctly even after schema upgrades. The `schema_version` field is embedded in every journal event.

---

## 5. Runtime Execution Flow

The canonical lifecycle of a task in AJA Runtime:

1. **Task created** → persisted to LanceDB *and* appended to journal as `task_created` event.
2. **Planner** evaluates objective and determines steps.
3. **ActivityContext** wraps each step; on re-entry, replays from journal instead of re-executing.
4. **Baton** (Arrow-serialized execution state + trace context) handed to worker.
5. **Worker** executes via `ExecutionManager` → `PTYTransport` or `PipeTransport`.
6. **Telemetry** (stdout, stderr, exit codes) written to `timeline.jsonl` under `.aja/executions/<session_id>/`.
7. **State transition** event appended to mission journal; projection updated.

---

## 6. Key Features

- **Replay-Authoritative Journal**: `.jsonl` event log is the single source of truth; LanceDB tables are derived projections.
- **Durable Activity Execution**: `ActivityContext` provides crash-safe, deterministic step re-execution via journal replay.
- **Schema-Versioned Events**: All events carry `schema_version`; `VersionedEventRehydrator` ensures forward compatibility.
- **Deterministic Projection Rebuild**: `aja rebuild-projections` replays the full journal to reconstruct all tables from scratch.
- **Cron Scheduling**: LanceDB-backed deterministic job scheduling with 3-minute hard interrupt limits.
- **Arrow IPC**: O(1) zero-copy state transfer overhead via Apache Arrow + Rust/PyO3.
- **Trace Propagation**: Context-safe trace IDs propagated across async execution boundaries and into Arrow baton metadata.
- **PTY/Pipe Transport**: Unified async I/O transport with cooperative PTY on Windows (ConPTY) and `loop.add_reader` on POSIX.
- **Execution Replay Viewer**: Inspect manifests, timelines, diffs, and stream logs for any past session.

---

## 7. CLI Reference

### Diagnostics
```bash
python -m aja doctor
```

### Run a Mission (Dry-Run)
```bash
python -m aja run "Perform project analysis" --dry-run
```

### Rebuild LanceDB Projections from Journal
```bash
python -m aja rebuild-projections
```
Replays all journal events through the mission reducer and re-populates all LanceDB read-projection tables. Run this after schema migrations or to recover from projection corruption.

### Execution History
```bash
python -m aja exec list
python -m aja exec show <session_id>
python -m aja exec timeline <session_id>
python -m aja exec diff <session_id>
python -m aja exec replay --latest
python -m aja exec cleanup
```

### Interactive TUI Dashboard
```bash
python -m aja tui
```

### Setup Wizard
```bash
python -m aja setup
```

---

## 8. V1 Certification Status

| Milestone | Description | Status |
|---|---|---|
| M1 | Journal is append-only; no mutation of existing events | ✅ Verified |
| M2 | Reducer is a pure function (same inputs → same outputs) | ✅ Verified |
| M3 | All state reads go through projections, not the journal | ✅ Verified |
| M4 | `ActivityContext` intercepts replay correctly | ✅ Verified |
| M5 | `ReplayDivergenceError` raised on divergence | ✅ Verified |
| M6 | `VersionedEventRehydrator` handles schema upgrades | ✅ Verified |
| M7 | `rebuild-projections` produces identical state to live | ✅ Verified |
| M8 | Full test suite is green under global regression | ✅ 223 passed |

**Certification**: All 6 release blockers resolved. Chaos test suite green. Release criteria documented and signed.

---

## 9. Architecture Philosophy

- **Event-Sourcing as the Foundation**: The journal is the system. Everything else is a derived view.
- **Deterministic Orchestration**: The engine must predictably execute, timeout, or fail. LLM nondeterminism is bounded by deterministic runtime constraints.
- **Observable Execution**: Every action, shell command, and decision is traced, journaled, and emittable to an event sink.
- **Explicit Ownership**: Data has single owners. The journal owns state; projections own reads; clients own presentation.
- **Infrastructure-First Design**: Fix system constraints (memory, IPC, sandbox) before adding AI features.

---

## 10. Rust + Python Hybrid Design

| Layer | Language | Responsibility |
|---|---|---|
| State serialization | Rust + PyO3 | Apache Arrow baton IPC, zero-copy memory mapping |
| Orchestration | Python | asyncio scheduling, client adapters, shell semantics |
| Persistence | Python + LanceDB | Event journals (`.jsonl`), projection tables |
| Telemetry | Python | TraceContextManager, structured JSON event emission |

---

## 11. Example Use Cases

- **Research Daemons**: Long-running background processes that aggregate and synthesize information over days, surviving reboots via journal replay.
- **Local Automation**: Safe, sandboxed scripts that manage local infrastructure or file systems with full audit trails.
- **Scheduled Workflows**: Recurring AI tasks (e.g., daily briefings, repository audits) via the cron scheduler.
- **Persistent Assistants**: Agents that retain long-term memory and context across reboots through event sourcing.
- **Terminal-Native Orchestration**: Headless workflows triggered directly via CLI pipelines.

---

## 12. Current Limitations

- **Resource Governance**: While sandboxing limits process space, granular CPU limits and network egress filtering are not yet strictly enforced on all platforms.
- **Copy-on-Write Overlays**: Complete filesystem isolation relies on patch-based diffs. Full `tmpfs` copy-on-write overlay support is pending.
- **Distributed Journal Sync**: The journal is currently local-filesystem-only. Distributed multi-host journal replication is a planned future capability.

---

## 13. Project Structure

```
libs/
  aja-core/
    aja/
      runtime/
        execution/       ← ExecutionManager, ActivityContext, Rehydrator
        journal/         ← Append-only event journal, event_schema.py
        mission/         ← MissionReducer, projection logic
        handover.py      ← Arrow Baton IPC (zero-copy)
      scheduler/
        cron_scheduler.py ← LanceDB-backed cron
      observability/
        telemetry.py     ← TraceContextManager
      tui/
        curses_tui.py    ← Live Curses dashboard
  aja-native/            ← Rust PyO3 native module (Arrow IPC)
tests/
  python/                ← 223-test suite (all green)
docs/
  architecture/          ← ARCHITECTURE.md, EXECUTION_MODEL.md, etc.
```

---

## 14. Development

### Run Unit Tests

```powershell
$env:PYTHONPATH="libs/aja-core"
& "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m pytest tests/python -v
```

Expected: **223 passed, 2 skipped, 0 failures**

### Run Dry-Run Simulation

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONPATH="libs/aja-core"
& "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m aja run "Perform project analysis" --dry-run
```

### Python Version

Always use the global Python 3.12.10 installation:
```
C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe
```

---

## 15. Changelog Highlights (v1.0)

| Phase | Change |
|---|---|
| Phase 1 | Canonical Execution Transport (PTY/Pipe FSM, `ExecutionManager`) |
| Phase 2 | Durable Execution (`ActivityContext`, `ContextVar` replay interception) |
| Phase 3 | Event-Sourced Missions (append-only journal, `MissionReducer`) |
| Phase 4 | Projection Rebuild CLI (`aja rebuild-projections`), chaos resilience |
| Phase 5 | Scheduler event sourcing (job state rehydrated from journal) |
| Phase 6 | Schema versioning (`event_schema.py`, `VersionedEventRehydrator`) |
