# Phase 20: Context-Aware Evaluator Reliability

## Objective
Harden the Agent evaluation system before transitioning to the Phase 11 planning layer by transitioning from global reliability metrics to a context-aware reliability model.

## Key Capabilities Added
1. **Context Extraction**: The system dynamically evaluates task context, estimating its `task_type` (e.g. coding, reasoning, retrieval, tool_use), `difficulty`, and `risk` using the `get_evaluation_context` utility in `evaluator.py`.
2. **Context-Sensitive Reliability Lookups**: Instead of using global judge metrics, the system queries the `evaluator_performance` schema filtered by `task_type` and `difficulty`. This eliminates hidden judge biases that might occur when a judge is good at simple text summarization but weak at complex reasoning.
3. **Context Drift Detection**: Continuously compares a judge's global reliability against its context-specific reliability to log `CONTEXT_DRIFT_DETECTED` if it diverges beyond expected thresholds.
4. **Reliability Weighted Vetoes**: The contextual reliability scores influence the weight of a veto, enabling strong contextual judges to trigger `HARD_VETO` and weaker contextual judges to trigger `SOFT_VETO`.
5. **Contextual Calibration**: The daily calibration (`run_calibration_tests`) extracts the task context when tracking Golden Tasks mismatch metrics and stores updates bound to that specific task context.

## State Transitions & Observability
- **`CONTEXTUAL_RELIABILITY_USED`**: Logged when a judge's reliability is pulled successfully for a specific task context.
- **`WEAK_JUDGE_DETECTED`**: Emitted when a judge's contextual reliability drops below the acceptable threshold (0.3).
- **`CONTEXT_DRIFT_DETECTED`**: Emitted when a judge is globally reliable but contextually failing.
- **`EVALUATOR_DRIFT_DETECTED`**: Meta-evaluation tracks individual drift during calibration loops.

## Database Migrations
- `evaluator_performance` table updated to include:
  - `task_type` (TEXT)
  - `difficulty` (TEXT)

This concludes the hardening of the decision engine, paving the way for the robust Phase 11 multi-step planning layer.
