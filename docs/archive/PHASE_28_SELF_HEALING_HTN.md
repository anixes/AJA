# Phase 28: Self-Healing HTN & Multi-Run Consensus
Status: IMPLEMENTED

## Objective
To eliminate the brittleness inherent in Large Language Model (LLM) generated planning structures (such as hallucinated node dependencies or invalid DAG formations) and ensure 99.9% mission logic reliability before the autonomous execution phase begins.

## 1. Structural Sanitizer (Self-Healing HTN)
The LLM occasionally generates Hierarchical Task Networks (HTNs) with structural violations, such as:
- Compound nodes depending on other compound nodes (which the executor cannot process).
- Missing primitive leaves.
- Self-referencing dependencies causing infinite loops.

### Implementation Details (`aja.planning.models`)
- **Semantic Healing**: We introduced a `from_dict` constructor override in the `PlanGraph` model. This method acts as a structural sanitizer during the Pydantic deserialization phase.
- **Dependency Re-Routing**: If the engine detects an illegal edge (e.g., pointing to a compound parent instead of a primitive child), it recursively searches the hierarchy and auto-re-routes the dependency to the correct executing leaf nodes.
- **Effect Propagation**: Missing context or state effects are heuristically populated based on sibling node definitions to prevent state fragmentation.

## 2. Multi-Run Consensus Planning
To further harden complex missions against stochastic variations in LLM generation, the planner implements a consensus mechanism.

### Implementation Details (`aja.planning.planner`)
- **Parallel Generation**: For tasks deemed highly complex (scored via `Task Complexity Evaluator`), the planner spawns multiple independent planning runs.
- **DAG Validation Check**: Each candidate plan is run through the `DAGValidator` (`aja.planning.dag_validator.py`), which uses strict Boolean and structural matching.
- **Consensus Selection**: The engine scores valid candidates and selects the most optimal HTN graph. 
- **Prompt Escaping**: The system prompt for the planner was hardened with escaped braces (`{{ }}`) to prevent template injection faults when defining JSON state schemas.

## 3. Results & Impact
- **Zero-Crash Execution**: By shifting validation to the deserialization and planning phases, the runtime worker (Muscle) is guaranteed a structurally sound sequence.
- **Stable Autonomy**: Resolves the "Verifier Rejection Loop" where the executor would repeatedly fail and re-plan due to minor formatting discrepancies or invalid edges.
