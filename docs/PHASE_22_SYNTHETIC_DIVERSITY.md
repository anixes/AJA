# Phase 22: Synthetic Diversity Layer (BETA)

## Overview
Phase 22 introduces a **Synthetic Diversity Layer** that ensures Agent generates structurally different candidate plans from a single model. This prevents "diversity collapse" where multiple generated plans are merely syntactic variations of the same flawed logic.

## Core Features

### 1. Multi-Perspective Generation
The planning engine (`planner.py`) now generates plans using multiple distinct "reasoning modes":
- **DEFAULT**: Standard optimal path.
- **RISK_ANALYSIS**: Focuses on failures, edge cases, and robustness.
- **MINIMAL**: Simplest possible plan with fewest steps.
- **AGGRESSIVE**: Fastest direct path, ignoring some safety tradeoffs.
- **SKEPTIC**: Challenges all previous assumptions and takes a fundamentally different approach.

### 2. Structured Reasoning Constraints
Each mode is paired with specific LLM configurations:
- **Temperature Tuning**: Ranging from 0.3 (Minimal) to 0.9 (Aggressive).
- **Depth Constraints**: Controlling how deep the task decomposition goes.
- **Reasoning Prompts**: Specific system instructions to force structural divergence.

### 3. Diversity Metrics & Scoring
The system calculates a `diversity_score` based on:
- **Semantic Similarity**: Using embeddings to ensure plans are conceptually distinct.
- **Structural Overlap**: Comparing the graph topology of the generated plans.
- **Tooling Variance**: Checking if different plans utilize different capability sets.

### 4. Diversity Collapse Safeguards
If the `disagreement_score` or `diversity_collapse_score` exceeds 0.75, the system automatically falls back to the **Stable** (Phase 21.6) execution mode to ensure reliability.

---

## Technical Details
- **Configuration**: `AGENTX_DIVERSITY_BETA = True` in `config.py`.
- **Logic Location**: `agent/planning/generator.py` and `agent/planning/planner.py`.
- **Interaction**: Integrated into the Generate-Verify-Select pipeline.
