# Phase 14: Research-Grade Multi-Plan Planning System

Phase 14 transforms the Agent planning architecture from a single-plan generator into an adaptive, research-aligned **Generate-Verify-Select** system. This prevents common planning failures such as hallucinations, structural loops, and repetitive mistakes.

## Architecture: Generator-Verifier-Selector

The system now follows a three-stage pipeline for every complex goal:

### 1. Adaptive Candidate Generation
- **Complexity Estimation**: Before planning, Agent estimates the task complexity (LOW, MEDIUM, HIGH) based on word count and semantic keywords.
- **Dynamic K-Candidates**:
    - LOW: 1 candidate (fast path).
    - MEDIUM: 3 candidates.
    - HIGH: 5 candidates.
- **Diversity Filtering**: Candidates are compared using structural Jaccard similarity. Variations that are too similar are rejected to ensure a broad "search space" of possible execution strategies.

### 2. Independent Verifier Agent
- **Bias Prevention**: The Verifier is an independent LLM instance that receives only the generated plan and the system state, with no access to the planner's internal logic.
- **Multi-Factor Analysis**: It evaluates:
    - **Logical Soundness**: Are the steps in a valid sequence?
    - **State Consistency**: Do effects align with preconditions?
    - **Risk Scoring**: How destructive or irreversible are the proposed actions?
- **Missing Preconditions**: Identifies state keys that the planner might have overlooked.

### 3. Multi-Plan Selector
- **Weighted Scoring**: Plans are ranked using a composite score involving:
    - Success probability (from method history).
    - Uncertainty (node-level).
    - Parallelism potential.
    - Token/Time cost.
    - Verifier confidence.
- **Risk Thresholds**: If a plan's risk score exceeds a threshold, the selector either picks the "Safest" candidate or escalates to the user.

## Failure Memory (Persistent Adaptation)
Agent now maintains a persistent `failures.json` store that records every failed execution attempt.
- **Similarity Penalization**: When generating new plans, the Scorer retrieves similar past failures via goal embeddings.
- **Infinite Loop Protection**: If a proposed plan is structurally similar to a previously failed attempt for the same goal, it receives a heavy "Failure Penalty," forcing the generator to seek a novel approach.

## Interfaces & Logic
- **`generator.py`**: Handles adaptive K-candidate production and diversity pruning.
- **`verifier.py`**: Independent LLM-based logical verification.
- **`selector.py`**: Weighted decision engine for picking the winning plan.
- **`failure_memory.py`**: Embedding-aware persistent store for recording and penalizing past errors.
- **`scorer.py`**: Updated to incorporate Verifier signals and Failure Memory penalties.
