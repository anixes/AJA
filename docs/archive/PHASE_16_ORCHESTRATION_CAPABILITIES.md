# Phase 16: Central Orchestration & Capabilities

Phase 16 transforms Agent from a reasoning engine into a **Transactional Cognitive Operating System**. It introduces a formal intermediate representation (PlanIR), a robust capability layer for real-world interaction, and an event-driven server for remote operation.

## 1. Canonical Execution IR (PlanIR)
Execution is now driven by a formal `PlanIR` rather than raw dictionaries.
- **Node Policies**: Every node carries explicit policies for `retry` count, `timeout`, and `idempotency`.
- **Compensation Policies**: Defines "Forward Recovery" actions (e.g., `git reset`) to be executed if a node fails after side-effects have occurred.
- **PlanIR Interface**: `agent/planning/ir.py` provides the schema for validating and serializing plans before execution.

## 2. Structured Capability System
All real-world actions are wrapped as `Capability` objects, ensuring strict interface contracts.
- **CapabilityResult**: Standardized output including `success`, `output`, `error`, and `state_delta`.
- **Sandboxing**: The `terminal.exec` capability includes security checks and execution bounds to prevent accidental system destruction.
- **Registry**: Central `CapabilityRegistry` resolves tools and sub-agents dynamically at runtime.

## 3. Agent-of-Agents (Specialization)
Agent can now delegate tasks to specialized sub-agents.
- **Sub-Agent Interface**: `CodingAgent` and `BrowserAgent` operate under strict resource envelopes (`max_steps`, `max_tokens`).
- **Autonomous Delegation**: The main coordinator uses `AgentCapability` to treat specialists as tools, maintaining top-level transactional control.

## 4. Event-Driven Runtime
The static execution loop has been replaced with a real-time event system.
- **Event Bus**: The `ReActExecutor` publishes events (`NODE_STARTED`, `NODE_SUCCESS`, `ROLLBACK`, etc.) to a global bus.
- **Failure Classification**: Errors are automatically classified as `TRANSIENT` (retryable), `LOGIC` (repairable), or `EXTERNAL` (escalation required).
- **Forward Recovery**: On failure, the system performs a state rollback AND executes the compensation action if defined in the policy.

## 5. Agent API Layer (Remote Control)
Agent now supports persistent, remote interaction via a FastAPI server.
- **Session Management**: Tracks user history and active plans across multiple interactions.
- **Async Task Queue**: Tasks submitted via API are processed by a background `agent_loop` to keep the interface responsive.
- **Real-Time Streaming**: Live execution updates are streamed to clients (e.g., mobile phones) via WebSockets.

## Interfaces & Logic
- **`ir.py`**: Canonical execution representation and policies.
- **`event_bus.py`**: Publisher/Subscriber bus for runtime events.
- **`capabilities/`**: Capability registry, base contracts, and sandboxed tools.
- **`server/`**: FastAPI implementation, task queueing, and Agent loop.
- **`agents/`**: Specialized sub-agent abstractions.
