# Phase 11: Parallel Plan Serializability & Verification

Phase 11 transitions Agent from reactive hierarchical execution to **conflict-aware parallel planning**. This phase implements the core infrastructure for executing independent task nodes concurrently while guaranteeing that the result is identical to a safe sequential execution (Conflict-Serializability).

## Core Objective
Implement a ReAct-style parallel executor that respects state dependencies, detects write/read conflicts, and provides a formal verification layer to prove execution consistency under stress.

## Key Components

### 1. Conflict-Aware Scheduler Wave Decomposition (`scheduler.py`)
The scheduler now decomposes a Hierarchical Task Network (HTN) into "waves" of parallelizable nodes.
- **Dependency Tracking**: Nodes only enter a wave if all parent dependencies are satisfied.
- **Wave Isolation**: Each wave represents a set of nodes that can safely run in parallel, provided they don't share state conflicts.

### 2. Parallel ReAct Executor (`react_executor.py`)
A robust execution engine that processes waves of tasks using concurrency.
- **Wave-Based Concurrency**: Executes batches of ready primitive nodes using `ThreadPoolExecutor`.
- **Escalation Safety**: Critical bug fix implemented to prevent infinite loops during failure escalation. If any node in a wave requires `ESCALATE`, the executor stops safely to allow operator intervention.
- **Cross-Platform Compatibility**: Sanitized all terminal outputs (removed non-ASCII emojis) to ensure reliability on Windows environments.

### 3. Serializability Verification Layer (`verification.py`)
A formal proof system to ensure parallel execution doesn't introduce race conditions or state corruption.
- **Sequential Baseline**: Ability to run a plan in a single-threaded, strictly ordered mode to capture a "ground truth" state.
- **Parallel Equivalence**: Compares the final `system_state` of parallel runs against the sequential baseline.
- **State Diffs**: Provides granular reporting on any key-value mismatches between execution modes.

### 4. Jitter & Stress Testing (`test_parallel_serializability.py`)
To expose rare race conditions, Phase 11 introduces artificial execution jitter.
- **Sub-millisecond Delays**: Random jitter introduced during state mutations to force thread interleaving.
- **Conflict Simulations**: Tests covering Write-Write, Read-Write, and Transitive state conflicts.
- **Circular Dependency Detection**: Validates that the scheduler correctly handles (and rejects) impossible state cycles.

### 5. Thread-Safe Execution Bridge (`execution_bridge.py`)
The bridge between the planner and the tools now enforces atomic state updates.
- **State Locking**: Uses `threading.Lock` to ensure that `system_state` mutations are atomic.
- **Trace Persistence**: Every node execution is logged with its preconditions, effects, and resulting state snapshot for post-mortem analysis.

## Interfaces

### Developer / Researcher
- **SerializabilityVerifier**: A diagnostic tool for validating new planning algorithms.
- **Parallel Trace Logs**: Stored in the tracker for auditability.

## Validation Invariants

| Invariant | Guarantee | Status |
| :--- | :--- | :--- |
| **1. Conflict Serializability** | Parallel execution state == Sequential execution state. | ✅ **VERIFIED** |
| **2. Deadlock Avoidance** | Batches are strictly ordered by topological wave structure. | ✅ **VERIFIED** |
| **3. Escalation Stop** | Failure-to-escalate stops execution waves immediately. | ✅ **VERIFIED** |
| **4. State Atomicity** | Parallel nodes cannot corrupt shared state keys. | ✅ **VERIFIED** |
| **5. Windows Reliability** | Clean Unicode-free logging for terminal compatibility. | ✅ **VERIFIED** |

## Implementation Details

The transition from reactive execution to planning is enabled by the `PlanGraph` structure, where nodes explicitly define `preconditions` and `effects`. 

### Conflict Detection & Serializability Logic

To guarantee that parallel execution is equivalent to sequential execution (Conflict-Serializability), the scheduler employs a dependency-aware batching algorithm in `react_executor.py`:

1.  **Conflict Identification**: Two nodes, A and B, conflict if:
    *   **Write-Write**: `A.effects ∩ B.effects` is not empty.
    *   **Read-Write**: `A.preconditions ∩ B.effects` is not empty.
    *   **Write-Read**: `A.effects ∩ B.preconditions` is not empty.
2.  **Constraint Enforcement**: If A and B conflict and A appears before B in the deterministic sequential plan, an edge `A → B` is added to a conflict graph.
3.  **Batching**: Nodes are executed in "parallel waves" where each wave consists of nodes with zero incoming edges in the conflict graph. This ensures that all conflicting operations are serialized according to their original plan order.

### Verification Workflow

The `verification.py` module provides a rigorous test harness:
1.  **Sequential Baseline**: The plan is executed in a single thread, capturing the exact `system_state` transition.
2.  **Parallel Execution**: The plan is executed concurrently using `ThreadPoolExecutor`.
3.  **Equivalence Assertion**: The final states of both runs are compared. Any discrepancy (race condition) triggers a failure and detailed diff report.
4.  **Jitter Stress Test**: Random delays are injected into state mutations to maximize the probability of exposing rare interleaving bugs.
