# AJA

**A local-first durable execution runtime and replay-authoritative orchestration substrate for autonomous systems.**

<!-- BADGES PLACEHOLDER -->
<!-- [![Build Status](https://img.shields.io/github/actions/workflow/status/org/aja/ci.yml?branch=main)](https://github.com/org/aja/actions) -->
<!-- [![Version](https://img.shields.io/pypi/v/aja.svg)](https://pypi.org/project/aja/) -->
<!-- [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT) -->
<!-- [![Rust: PyO3](https://img.shields.io/badge/Rust-PyO3-orange.svg)](https://pyo3.rs/) -->

AJA provides the execution infrastructure required to run autonomous workflows safely and deterministically on local hardware. It replaces fragile agentic scripts with a robust, event-sourced runtime that guarantees state persistence, deterministic replay, and crash-consistent recovery.

---

## What AJA Is

AJA is a systems-level orchestration substrate designed for autonomous operations. It treats agentic workflows as long-running, durable compute processes rather than ephemeral chat loops.

* **Local-First Durable Execution Runtime**: Ensures workflows can survive process restarts, system crashes, and network partitions without losing state.
* **Replay-Authoritative Orchestration**: The system state is strictly derived from an append-only execution journal. If it isn't in the journal, it didn't happen.
* **Event-Sourced Infrastructure**: Every decision, command, and side effect is durably journaled before execution, enabling deterministic reconstruction of any workflow.

With AJA, operators can build research daemons, local infrastructure automation, and scheduled workflows that run continuously for days, surviving machine reboots and gracefully recovering from failures.

---

## Why AJA Exists

Most autonomous agent frameworks prioritize prompt engineering and LLM chain logic, leaving execution infrastructure as an afterthought. This results in:
* **Fragile Scripts**: Workflows that restart from zero when an API call times out or a process crashes.
* **Nondeterministic Execution**: Unpredictable loops where state mutations are lost in memory.
* **Log-Only Observability**: Systems where debugging relies on grep-ing unstructured logs rather than inspecting deterministic execution graphs.

AJA exists to invert this model. It provides:
* **Deterministic Replay**: The ability to reconstruct exact execution states by replaying the event journal.
* **Crash Recovery**: Workflows resume exactly where they left off after a system interruption.
* **Durable Side Effects**: External mutations are wrapped in durable activities, ensuring they are executed exactly once.
* **Execution Lineage**: Strict isolation and auditable trails for every action taken by the system.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Replay-Authoritative Orchestration** | The append-only `.jsonl` journal is the single source of truth. All runtime state is a derived projection. |
| **Durable Activities** | Execution steps are wrapped in context managers that intercept live execution and safely replay historical results during recovery. |
| **Event-Sourced Rehydration** | Deterministically reconstruct state from zero. Any divergence between live execution and historical replay is treated as a fatal error. |
| **Rust Acceleration (PyO3/Arrow)** | High-performance, zero-copy inter-process communication for state transfer via Apache Arrow baton caches. |
| **PTY Execution Runtime** | Unified async I/O transport providing cooperative PTY orchestration (ConPTY on Windows, POSIX PTYs on Linux/macOS). |
| **Schema Versioning** | Forward-compatible event definitions ensuring historical journals can always be replayed safely as the platform evolves. |
| **Operator Tooling** | Built-in CLI for diagnostics (`aja doctor`), setup (`aja setup`), and rebuilding projections (`aja rebuild-projections`). |

---

## Quick Install

AJA uses a unified `maturin` build system to compile the Rust native extensions and install the Python runtime simultaneously.

### Prerequisites
* Python 3.11+
* Rust Stable Toolchain

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/aja.git
cd aja

# Install the build backend
pip install maturin

# Build and install the unified package
maturin develop --release
# Alternatively: pip install .[all]
```

Verify the installation and runtime dependencies:
```bash
python -m aja doctor
```

---

## Quickstart

### 1. Initialize the Runtime
Initialize the runtime environment, which provisions the `AJA_DATA_DIR` and necessary LanceDB vector stores.

```bash
python -m aja setup
```
<!-- SUGGESTED SCREENSHOT: aja setup interactive CLI output -->

### 2. Run a Simulated Workflow
Run a dry-run simulation to audit potential shell executions against safety blocks without mutating local files.

```bash
python -m aja run "Perform repository analysis" --dry-run
```
<!-- SUGGESTED SCREENSHOT: Curses TUI showing the HTN DAG and live execution stream -->

### 3. Inspect Replay History
View the deterministic execution timeline for past sessions.

```bash
python -m aja exec list
python -m aja exec timeline <session_id>
```

---

## Architecture Overview

AJA enforces a strict separation between orchestration, durable persistence, and execution transport.

<!-- SUGGESTED DIAGRAM: Layered architecture showing Orchestrator -> Journal -> Projections -> Execution Workers -->

* **Orchestration Layer**: Manages the deterministic sequencing of tasks and handles control flow, acting as the primary state machine.
* **Durable Activity Layer**: Wraps side-effecting code. During normal execution, it runs the code and journals the result. During recovery, it returns the journaled result without re-executing.
* **Journal & Replay Layer**: The append-only `.jsonl` event log acts as the absolute authority. The `EventRehydrator` replays this log to reconstruct state.
* **Projection Layer**: LanceDB read-projections are deterministically built from the journal. They serve fast state queries but hold no authority.
* **Execution Transport Layer**: Provides process isolation via PTY orchestration, safely running commands and capturing `stdout`/`stderr` lineage.
* **Rust Acceleration Layer**: Handles heavy serialization and state transport using Apache Arrow IPC batons, bypassing Python's GIL for core I/O.

---

## Durable Execution Model

The core invariant of AJA is **replay determinism**. 

1. **Event Sourcing**: When an execution step occurs, an event is atomically appended to the journal. The runtime state is then updated via a pure function reducer.
2. **Crash Recovery**: If the system crashes, AJA does not restart the workflow. Instead, it replays the journal.
3. **Durable Activities**: During replay, when the orchestrator encounters a previously completed side effect (e.g., a network call or shell command), the `ActivityContext` intercepts the call, prevents execution, and returns the historical result.
4. **Lineage Isolation**: Every task execution is strictly scoped. Output payloads, exit codes, and trace IDs are durably logged, ensuring perfect audibility.

---

## Repository Structure

```text
aja/
├── libs/
│   └── aja-core/               # Core Python Orchestration Runtime
│       ├── aja/runtime/        # Rehydrator, Journal, and Durable Activities
│       ├── aja/scheduler/      # Deterministic Cron Execution
│       └── aja/observability/  # TraceContextManager & Telemetry
├── packages/
│   └── aja-native/             # Rust acceleration layer (PyO3 + Apache Arrow)
├── tests/
│   └── python/                 # Pytest suite ensuring replay determinism
├── docs/                       # Architecture specs and operator manuals
├── tools/                      # Release and development tooling
└── pyproject.toml              # Unified Maturin build manifest
```

---

## Operator Tooling

AJA includes built-in operational tooling designed for systems engineers managing local environments.

* **`aja doctor`**: Validates the health of the host system, ensuring Rust native modules, vector stores, and required binaries are correctly mapped.
* **`aja rebuild-projections`**: Discards all read-only LanceDB tables and deterministically rebuilds them from the append-only journal.
* **`AJA_DATA_DIR`**: A strictly enforced environment boundary that contains all execution state, keeping the host system clean.
* **`aja tui`**: A local curses-based dashboard providing real-time visibility into the HTN (Hierarchical Task Network) DAG, tailing logs, and system metrics.

---

## Development & Contributing

### Local Setup
Ensure you have Python 3.12+ and Rust installed. 
```bash
pip install -e .[dev]
```

### Testing
AJA maintains a strict testing philosophy. Any change that breaks replay determinism is a failed build.
```bash
python -m pytest tests/python -v
```

### Replay Certification Philosophy
We treat backwards compatibility of the event journal as a strict requirement. When altering core execution logic, you must ensure that historical journals can still be cleanly rehydrated by the `VersionedEventRehydrator`.

---

## Roadmap

* **Replay Certification**: Formalized compliance tooling to verify older journals against newer schema definitions.
* **Snapshotting**: Periodic state snapshots to reduce replay time on infinitely running research daemons.
* **Deterministic Concurrency**: Multi-threaded durable activities with strictly ordered event interleaving.
* **Release Engineering**: Pre-compiled binary distributions for isolated installation without a local Rust toolchain.
* **Operational Hardening**: Granular network egress filtering and copy-on-write overlay filesystems for strict sandbox isolation.

---

## Acknowledgements

AJA draws architectural inspiration from modern durable execution systems like Temporal, robust event-sourced architectures, and the Dapr runtime philosophy.

---

## License

[MIT License](LICENSE)
