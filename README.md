# AJA Runtime

**A local-first orchestration runtime and execution substrate for autonomous agents.**

---

## 1. What AJA Runtime Is

AJA Runtime is a persistent workflow engine and systems-level orchestration substrate. It provides the core execution infrastructure required to run autonomous agents reliably on local hardware. 

Instead of treating agents as transient chat loops, AJA treats agentic workflows as **standard scheduled compute**. It manages state persistence, process isolation, inter-process communication (IPC), deterministic scheduling, and execution telemetry, allowing developers to build robust AI agents without fighting the underlying infrastructure.

## 2. What AJA Runtime is NOT

- **Not a chatbot or "Jarvis clone":** AJA is the runtime infrastructure *underneath* the assistant, not the conversational interface itself.

- **Not a prompt-engineering framework:** AJA does not compete with LangChain or LlamaIndex. It focuses on the execution environment, not the LLM chain.

## 3. Why AJA Exists

Current agent ecosystems focus too heavily on prompt generation and too little on execution guarantees. Building reliable agents requires robust systems infrastructure: durable memory, fault-tolerant scheduling, safe execution sandboxes, and zero-copy state persistence. 

AJA exists to provide this local-first execution layer. Execution infrastructure matters more than prompt orchestration when building systems that can safely modify local files, execute shell commands, and run unattended for days.

## 4. Core Architecture

AJA's architecture separates the runtime from the presentation layer (CLI, TUI, Telegram, Web), ensuring the core orchestration logic remains isolated and deterministic.

```text
  +-------------------+        +--------------------+
  |  Clients / UIs    |        |  Scheduled Tasks   |
  | (CLI, TUI, HTTP)  |        | (LanceDB + Cron)   |
  +--------+----------+        +---------+----------+
           |                             |
           v                             v
  +-------------------------------------------------+
  |                  AJA Runtime                    |
  |  +-----------------+       +-----------------+  |
  |  |  Orchestration  | ----> | Trace Telemetry |  |
  |  +-----------------+       +-----------------+  |
  |          |                                      |
  |          v                                      |
  |  +-----------------+       +-----------------+  |
  |  | Rust Baton IPC  | ----> |   Lance Store   |  |
  |  +-----------------+       +-----------------+  |
  +----------+--------------------------------------+
             |
             v
  +-------------------------------------------------+
  |                Execution Workers                |
  |  [ Docker Sandbox ]   [ Direct Subprocess ]     |
  +-------------------------------------------------+
```

### Major Subsystems
- **Runtime Scheduler**: Deterministic cron and duration-based scheduling backed by LanceDB.
- **Baton IPC**: Inter-process communication using Apache Arrow for zero-copy state transfer.
- **Orchestration Engine**: Translates high-level tasks into executable steps within strict time limits.
- **Execution Sandbox**: Docker-based process isolation for shell commands.
- **Trace Telemetry**: Context-variable based trace propagation for complete execution observability.
- **Persistent Memory**: LanceDB vector and tabular stores for long-term task and event retention.

## 5. Runtime Execution Flow

The canonical lifecycle of a task in AJA Runtime follows a strict, observable path:

1. **Task** is created and persisted to LanceDB.
2. **Planner** evaluates the objective and determines the steps.
3. **Baton** containing the execution state and trace context is generated and serialized via Rust Arrow.
4. **Worker** picks up the baton (locally or remotely).
5. **Execution** occurs safely within the sandbox boundaries.
6. **Telemetry** (stdout, stderr, exit codes) is emitted to the Event Sink.
7. **Persistence** captures the final state, updating the task store and flushing the trace tree.

## 6. Key Features

- **Persistent Execution**: Tasks survive process restarts and crashes.
- **Cron Scheduling**: Built-in deterministic job scheduling with hard interrupt limits.
- **Arrow IPC**: O(1) state transfer overhead using Apache Arrow memory mapping.
- **LanceDB Memory**: Unified vector and metadata storage for execution history.
- **Trace Propagation**: Context-safe trace IDs mapped across async execution boundaries.
- **Sandbox Execution**: Docker-isolated execution with explicit resource bounds (Memory, CPU, Network).
- **Runtime APIs**: Clean decoupling between the core engine and external client interfaces.

## 7. Current Capabilities

**Stable & Production-Ready:**
- Task persistence and state recovery
- Cron scheduling and timeout enforcement
- Baton IPC and Arrow serialization
- Trace propagation and JSON logging
- Isolated sandbox execution with PTY streaming
- Execution Replay & Visualization tooling (artifact-backed workspace diffs & timeline inspection)

**Evolving:**
- Orchestration DAGs and multi-step planning
- Distributed remote execution (HTTP Baton transmission)

## 8. Architecture Philosophy

- **Deterministic Orchestration**: The engine must predictably execute, timeout, or fail. Nondeterministic LLM calls are bounded by strict systems constraints.
- **Observable Execution**: Every action, shell command, and decision is traced and emitted to an event sink.
- **Explicit Ownership**: Data has single owners. The runtime owns execution; the clients own presentation.
- **Composable Runtime APIs**: Interfaces are built for programmatic consumption by future agent frameworks.
- **Infrastructure-First Design**: Fix the system constraints (memory, IPC, sandbox) before adding AI features.

## 9. Rust + Python Hybrid Design

AJA uses a hybrid runtime to maximize performance and developer velocity:

- **What Rust Owns**: Baton serialization (Apache Arrow), PyO3 IPC boundaries, trajectory compression, and token counting. 
  - *Why?* Zero-copy state transfer and O(1) memory mapping completely eliminate the massive overhead of moving multi-turn LLM context windows between processes.
- **What Python Owns**: Orchestration logic, the `asyncio` scheduling loop, client integrations, and shell execution wrappers. 
  - *Why?* Python provides unparalleled velocity for API integrations, async I/O, and shell semantics without premature optimization.

## 10. Example Use Cases

- **Research Daemons**: Long-running background processes that aggregate and synthesize information over days.
- **Local Automation**: Safe, sandboxed scripts that manage local infrastructure or file systems.
- **Scheduled Workflows**: Recurring AI tasks (e.g., daily briefings, repository audits).
- **Persistent Assistants**: Agents that retain long-term memory and context across reboots.
- **Terminal-Native Orchestration**: Headless workflows triggered directly via CLI pipelines.

## 11. Current Limitations

Please note the following architectural gaps:
- **Resource Governance**: While sandboxing limits process space, execution policies (e.g., granular CPU limits, network egress filtering) are not yet strictly enforced.
- **Copy-on-Write Overlays**: Complete filesystem isolation relies on patch-based diffs. Full `tmpfs` copy-on-write overlay support is pending.

## 12. Roadmap (Next 6 Months)

1. **Execution Policy & Resource Governance**: Implement constraints for resource utilization and execution capabilities.
2. **Copy-on-Write Workspaces**: Move from git-patch diffs to isolated `tmpfs` execution overlays for complete filesystem safety.
3. **OpenTelemetry Integration**: Standardize the `TraceStore` to canonical OTel schemas.
4. **API Stabilization**: Lock the schema for LanceDB TaskStore and EventSink.

## 13. Project Structure

- `libs/aja-core/`: Python orchestration, scheduler, observability, and sandbox runtime.
- `libs/aja-native/`: Rust implementation of Arrow IPC and high-performance state compression.
- `tests/`: Comprehensive Python and TypeScript test suites verifying boundary integrity.

## 14. Quick Start

Run the systems diagnostics to verify native modules and dependencies:
```bash
python -m aja doctor
```

Launch the Curses TUI Live Dashboard:
```bash
python -m aja tui
```

Launch the Execution Replay Viewer:
```bash
python -m aja exec replay --latest
```

## 15. Development

Ensure you are using the global Python environment.

Run Python unit tests (verifies IPC, scheduler, tracing):
```bash
export PYTHONPATH="libs/aja-core"
python -m pytest tests/python
```
