# Phase 15: Transactional Execution & Local Repair

Phase 15 upgrades Agent execution from static "run-and-fail" to a robust, self-healing **Transactional Runtime**. It introduces the ability to rollback state after failures and repair specific parts of a plan without re-executing the entire mission.

## 1. Transactional State Engine
Execution is now treated as a sequence of discrete transactions.
- **Execution Log**: Every state change is recorded in `execution_log.py`, creating a versioned history of the system state.
- **State Checkpoints**: Before any node executes, the `ExecutionBridge` takes a deep-copy snapshot of the system state.
- **Atomic Rollbacks**: If a node fails, Agent performs an `UNDO` operation, reverting the system state to the exact pre-execution snapshot. This prevents "state pollution" where a partially failed action leaves the environment in an inconsistent state.

## 2. Verifier-Guided Runtime
The system no longer blindly executes plans. It performs "just-in-time" safety checks:
- **Step-Level Verification**: Immediately before execution, the `verify_step` function performs a deterministic logic check.
- **Precondition Validation**: It verifies that all necessary state variables are present and match expected values.
- **Drift Detection**: If the environment has changed since the plan was first generated (state drift), the verifier flags the node as "Unsafe," triggering an immediate repair.

## 3. Localized Repair Engine
Instead of abandoning a plan on failure, Agent now performs surgical patches:
- **Failure Scope Detection**: When a node fails, the system identifies the "blast radius"—the failed node and all downstream dependent nodes.
- **Subtree Extraction**: The broken part of the plan is extracted from the global DAG.
- **Generative Repair**: The `Planner` is invoked specifically for the broken subtree's goal. It generates a "patch plan" that accounts for the current (failed) state.
- **Surgical Splicing**: The old, broken subtree is removed, and the new repaired subtree is spliced back into the original plan, rewiring all outer dependencies.

## 4. Execution Loop Orchestration
The `ReActExecutor` run loop has been refactored into a high-reliability cycle:
1. **Ready Nodes**: Identify parallel-ready tasks.
2. **Checkpoint**: Snapshot state.
3. **Verify**: Perform pre-execution logic checks.
4. **Execute**: Run the tool/action.
5. **Rollback**: On failure, revert state instantly.
6. **Repair**: Attempt localized patching (Max 2 attempts per node).
7. **Resume**: Continue execution with the repaired plan.

## Interfaces & Logic
- **`execution_log.py`**: Versioned state logging and snapshot management.
- **`execution_bridge.py`**: Integrated checkpointing and rollback hooks.
- **`replanner.py`**: Failure scope detection and subtree splicing logic.
- **`react_executor.py`**: Orchestrates the transactional repair loop with `MAX_REPAIR_ATTEMPTS` guards.
