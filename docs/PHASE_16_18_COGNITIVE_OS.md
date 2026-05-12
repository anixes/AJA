# AgentX: Transactional Cognitive Operating System
## Phases 16-18 Implementation Overview

AgentX has evolved into a research-grade **Transactional Cognitive Operating System**. It moves beyond simple "planning and execution" into a robust runtime environment capable of secure orchestration, state recovery, and human-in-the-loop control.

---

### 🧠 1. Core Execution: PlanIR & Versioning
The system now uses a **PlanIR (Intermediate Representation)** for all execution.
- **PlanVersion**: Every repair cycle or structural modification creates a new `PlanVersion`. This allows for full lineage tracking of how a plan evolved from the initial goal to the final successful execution.
- **Node Policies**: Nodes now support granular policies for `retry`, `timeout`, and `compensation`.

### 🛡️ 2. Security: Hard Sandbox (Docker)
All terminal-based operations are isolated via **Hard Container Sandboxing**.
- **Container Isolation**: Commands run in ephemeral `alpine` Docker containers.
- **Resource Constraints**:
    - `network=none`: Prevents data exfiltration.
    - `read-only`: Prevents root filesystem tampering.
    - `memory=256m`, `cpus=0.5`: Prevents resource exhaustion.
- **Permission Engine**: A centralized permission system validates every command against blocked keywords (e.g., `rm -rf`, `mkfs`) before it ever reaches the sandbox.

### 🚦 3. Human-in-the-Loop (HITL) Control
The runtime is no longer a "black box." It is fully controllable via the AgentX API Server.
- **Risk Gates**: Nodes with a `risk >= 0.8` trigger an automatic execution interrupt.
- **Async Interrupts**: The `ReActExecutor` supports pausing, resuming, and modifying the plan mid-flight.
- **Control API**:
    - `/approve`: Explicitly authorize high-risk actions.
    - `/reject`: Block an action and force the agent into failure-recovery mode.
    - `/modify`: Manually edit nodes before execution.

### 📊 4. Observability & Event-Driven Runtime
A global **EventBus** provides real-time system-wide visibility.
- **Telemetry**: Every node start, success, failure, and rollback is broadcasted.
- **TraceStore**: A complete JSON-serializable log of every execution step, state change, and repair record.
- **Metrics**: Real-time tracking of Success Rate, Repair Rate, and Latency.

---

### 🛠️ Key Components
- **`ReActExecutor`**: The heart of the transactional engine.
- **`ExecutionBridge`**: Manages state checkpoints and rollbacks.
- **`Replanner`**: Handles autonomous graph repair.
- **`AgentX API`**: FastAPI-based remote control interface.
