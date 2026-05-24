# Agent Uncertainty Contract
**Status:** Mandatory Behavioral Core (Phase 21.5)
**Scope:** Entire Agent System (Execution, Decision, Planning, Memory)

This document establishes the strict behavioral rules regarding uncertainty within the Agent system. **This is not a feature; it is a fundamental system guarantee.** All future layers, especially the Phase 11 Planning Layer, MUST adhere to these rules.

## Core Mandate
Every decision, plan step, and execution MUST carry and reason over uncertainty. The system must optimize and guard against `trajectory_uncertainty`, not just immediate step correctness.

---

### RULE 1 — Uncertainty as a First-Class Signal
Every execution or evaluation step MUST return a structured object that includes:
- `result` (the actual output)
- `uncertainty_score` (float, 0.0 to 1.0)
- `risk_score` (float, 0.0 to 1.0)
The system MUST NEVER discard the `uncertainty_score`.

### RULE 2 — Trajectory Awareness
The system maintains `task_uncertainty = Σ(step_uncertainty with decay)` at all times.
Future decisions (such as next-step selection, retry loops, and replanning) MUST depend on the accumulated `task_uncertainty`.

### RULE 3 — Hard Stop Condition
If `task_uncertainty > MAX_TASK_UNCERTAINTY` (default 0.8):
1. **STOP** execution immediately.
2. **ESCALATE** to operator (`ASK`).
3. **LOG** `SYSTEM_UNCERTAINTY_EXCEEDED`.

### RULE 4 — Planning Constraint
The Planning Orchestrator (Phase 11+) MUST produce a sequence of step objects containing expected bounds, rather than a plain string list.
Required schema per step:
```json
{
  "step": "description of step",
  "expected_uncertainty": float,
  "risk_level": "string"
}
```

### RULE 5 — Plan Execution Control
During execution of a planned sequence:
If `actual_uncertainty > expected_uncertainty` for a given step, the system MUST trigger:
- **REPLAN** (re-evaluate the remaining sequence), OR
- **ESCALATE** (fall back to safety).

### RULE 6 — Multi-Step Safeguard
If:
- 2 or more consecutive steps evaluate as `UNCERTAIN`, OR
- The `uncertainty_score` repeatedly exceeds step thresholds
Then:
- The system MUST terminate the chain early and log `CHAIN_UNCERTAINTY_EXCEEDED`.

### RULE 7 — Strategy Selection Module Integration
The Strategy Selection Module (`agent/decision/engine.py`) MUST bias against complexity when uncertainty is high.
If `task_uncertainty` is high:
- **PREFER**: `ASK` (escalate) or `NEW` (safe fallback execution).
- **AVOID**: `Hierarchical Execution` (multi-step skill chains) or deep logical paths.

### RULE 8 — Anti-Propagation Constraint
Before executing any step in a sequence:
If the prior step's uncertainty was HIGH, the system MUST require either:
- Re-validation of the previous step's output.
- Grounding via external fact-checking or explicit tool calls.

### RULE 9 — Memory Integrity
The system MUST NOT treat previous outputs as absolute facts.
When storing or retrieving data from Memory, it must capture:
```json
{
  "value": "the data",
  "confidence": float,
  "source_type": "tool | inference | guess"
}
```

### RULE 10 — Observability
The following forensic events MUST be logged:
- `UNCERTAINTY_ACCUMULATED`
- `SYSTEM_UNCERTAINTY_EXCEEDED`
- `CHAIN_UNCERTAINTY_EXCEEDED`
- `HIGH_UNCERTAINTY_DECISION`
- `REPLAN_TRIGGERED`

These events must be exposed in CLI diagnostic tools (e.g., `agent explain <task_id>`).

---

## System Guarantees
By adhering to these rules, the Agent system guarantees:
1. The system stops before cascaded failures occur.
2. Long-horizon error accumulation is strictly mathematically controlled.
3. The planner fundamentally respects and bounds uncertainty.
4. Evaluation signals propagate globally through the execution context.
5. The system aggressively avoids hallucination drift.

## Strict Prohibitions
The system must **NEVER**:
- Ignore accumulated uncertainty.
- Execute long chains blindly.
- Assume previous step outputs are objectively correct without tracking confidence.
- Treat automated evaluation as the final, absolute truth.
