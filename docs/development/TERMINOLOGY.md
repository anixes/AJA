# Canonical Terminology Guide

To prevent architectural drift and maintain a professional, systems-oriented codebase, all documentation, variable names, and architectural discussions **must** adhere to this canonical terminology. 

Avoid "AGI" hype, anthropomorphic language ("thinking", "memory" as a human concept), and ambiguous buzzwords.

## Core Architecture Definitions

### 1. Runtime
The foundational orchestration substrate (Scheduler + IPC + Observability + Persistence) that routes tasks and handles lifecycle management. **AJA is a Runtime, not a Chatbot.**

### 2. Orchestration / Orchestrator
The deterministic logic engine that translates a high-level `Task` into a series of executable steps within strict time limits.

### 3. Worker
A sandboxed entity, process, or physical node responsible for executing a specific `Baton`. Workers do not plan; they execute.

### 4. Baton
A serialized, immutable snapshot of execution state and context (via Apache Arrow) transferred between the runtime and workers.

### 5. Scheduler
The LanceDB-backed subsystem (`cron_scheduler.py`) responsible for deterministic, time-bound task triggering.

### 6. Execution Environment / Sandbox
The bounded OS-level environment (`sandbox.py`) where a Worker executes shell commands. Typically a Docker container with restricted CPU, Memory, and Network.

### 7. Task / Job
A concrete objective persisted in the `LanceRuntimeTaskStore` with an associated state (`scheduled`, `in_progress`, `completed`, `failed`).

## Observability Definitions

### 8. Trace
A unique execution lineage context (`trace_id`) propagated across async and process boundaries to track the full lifecycle of a Task.

### 9. Event Sink
The decoupled message bus (`RuntimeEventSink`) where the Orchestrator and Workers flush telemetry, stdout, and state transitions.

## Boundary Definitions

### 10. Client / Presenter / Gateway
An external interface (CLI, TUI, HTTP API, Telegram) that consumes the runtime API. **Clients do not own state or orchestration logic.**

### 11. Compatibility Layer / Legacy Surface
Older code (often from the "Jarvis assistant" era) that has been boxed into adapters to maintain backward compatibility without polluting the core runtime logic.
