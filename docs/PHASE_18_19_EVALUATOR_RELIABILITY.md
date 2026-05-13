# Phase 18-19: Risk-Aware Correctness & Evaluator Reliability

The **Risk-Aware Correctness Pipeline** and **Evaluator Reliability Tracking** represent a major upgrade to Agent's decision gate. The system has shifted from a simple consensus model to a probabilistic, reliability-weighted meta-evaluation system that detects and suppresses its own biases.

## Core Objective
Ensure that autonomous execution is gated by a trustworthy verification layer that accounts for individual model strengths, historical performance, and the inherent risk of the task.

## Key Components

### 1. Risk-Aware Correctness (`evaluator.py`)
Evaluation is no longer binary. Every verification pass returns a structured risk profile.
- **Probabilistic Risk Scoring**: Calculates a `risk_score` (0.0 to 1.0) based on weighted consensus and execution confidence.
- **Risk-Aware Convergence**: If the agent loop detects convergence (stability), it is only allowed to finish if the `risk_score` is below the safety threshold. High-risk convergence triggers a `HIGH_RISK_CONVERGENCE` event and forces operator escalation.
- **Nuanced Outcomes**: The system differentiates between `PASS`, `FAIL`, and `UNCERTAIN`. `UNCERTAIN` results are treated as failures in the main loop to prevent "hallucination success."

### 2. Evaluator Reliability Tracking (`metrics.py`)
Agent now maintains a long-term "reputation" for every model used in the evaluation pipeline.
- **Performance Schema**: The `evaluator_performance` table tracks:
    - `total_evals`: Total number of verification passes.
    - `false_success_count`: Number of times the judge missed a failure (False Positive).
    - `veto_count`: Number of times this judge correctly identified a failure others missed.
    - `disagreement_count`: How often this judge diverges from the consensus.
- **Dynamic Reliability Formula**: Reliability is calculated as a product of ground-truth accuracy and the inverse of the false success rate.
- **Weak Judge Suppression**: Judges with reliability scores below `0.3` are automatically skipped during evaluation to prevent "weak consensus" from diluting the results.

### 3. Reliability-Weighted Veto & Voting
- **Weighted Voting**: The final decision is calculated using `score = Σ (eval_result * reliability)`. High-reliability models have more "votes" in the outcome.
- **Tiered Veto System**:
    - **HARD VETO**: Triggered if a judge with high reliability (`rel >= 0.5`) issues a `FALSE_SUCCESS` signal. This immediately fails the task.
    - **SOFT VETO**: Triggered by lower-reliability judges. It marks the task as `UNCERTAIN` and raises the `risk_score`, but allows for potential recovery or consensus if other high-reliability models disagree.

### 4. Meta-Evaluation & Calibration Loop (`calibration.py`)
The system monitors the health of the evaluation gate itself.
- **Individual Calibration**: `run_calibration_tests()` replays "Golden Tasks" against *each* individual evaluator to calibrate their reliability scores against ground truth.
- **Bias Detection**: `evaluate_evaluator()` scans for performance patterns:
    - `BIAS_PATTERN (Yes-man)`: Flags judges that consistently approve incorrect results.
    - `INCONSISTENT_JUDGE`: Flags judges with high disagreement rates.
- **Diversity Enforcement**: Logs a warning if the active `EVALUATORS` do not span at least two distinct model families (e.g., OpenAI and Anthropic), mitigating the risk of correlated logic failures.

## Interfaces & Observability

### CLI Commands
- `agent metrics`: Now includes a detailed breakdown of evaluator reliability and performance.
- `agent explain <task_id>`: Surfaces `VETO_SOURCE` and `RISK_SCORE` for every verification attempt.
- `agent doctor`: Validates the health of the evaluator pipeline and reports on detected drift.

### Event Log
| Event | Meaning |
| :--- | :--- |
| `HIGH_RISK_CONVERGENCE` | Agent stopped but the result is too risky to trust. |
| `WEAK_JUDGE_DETECTED` | An evaluator was skipped due to low historical reliability. |
| `HARD_VETO` / `SOFT_VETO` | A judge explicitly rejected the execution result. |
| `BIAS_PATTERN_DETECTED` | A judge is showing statistically significant bias (e.g., False Positives). |
| `EVALUATOR_DRIFT_DETECTED` | Calibration against Golden Tasks shows a drop in performance. |

## Validation Invariants

| Invariant | Guarantee | Status |
| :--- | :--- | :--- |
| **1. Weighted Consensus** | Decisions favor models with proven accuracy over "yes-man" models. | ✅ **VERIFIED** |
| **2. Reliability Gating** | Weak judges (< 0.3) cannot influence the final risk score. | ✅ **VERIFIED** |
| **3. Risk-Aware Stop** | The agent loop cannot finish a task if the risk score is high. | ✅ **VERIFIED** |
| **4. Family Diversity** | System warns if consensus is only sought from a single model family. | ✅ **VERIFIED** |
| **5. Continuous Calibration** | Judge reliability scores are updated during every golden task run. | ✅ **VERIFIED** |

---
*Generated via Agent Hardening Pass (Phases 18-19) on 2026-04-30.*
