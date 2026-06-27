---
name: critic-auditing
description: How to use and interpret the Critic agent — adversarial review of worker output inside GoalSwarmSession.
---
# Critic Auditing

The Critic is the third role in the Planner → Worker → Critic triad. It runs after
every worker iteration and either approves the result or feeds structured failure
context back into the next planning cycle.

## 1. How the Critic is invoked

`GoalSwarmSession` calls `critic_engine.execute_direct()` automatically after each
worker pass. You do not invoke it manually during a normal swarm run.

```
Iteration N:
  1. Planner generates / refines the HTN plan.
  2. Worker executes tool calls against the plan.
  3. Critic reviews worker output → emits GOAL_COMPLETE or GOAL_FAILED:<reason>.
  4. If GOAL_FAILED, failure_context is injected into iteration N+1.
```

## 2. Configuring the Critic model

The Critic should use a *different* model from the Worker to provide genuine
adversarial separation.

```bash
# .env or shell export
AJA_CRITIC_MODEL=openai:o1-mini      # reasoning model for critique
AJA_WORKER_MODEL=google:gemini-2.0-flash  # fast model for execution
AJA_PLANNER_MODEL=openai:gpt-4o      # powerful model for planning
```

Or set in `aja.json`:
```json
"models": {
  "planner": "openai:gpt-4o",
  "worker":  "google:gemini-2.0-flash",
  "critic":  "openai:o1-mini"
}
```

## 3. Critic system prompt expectations

The Critic receives the full worker output and is prompted to:
- Verify the stated goal is *actually* achieved (not just claimed).
- Identify incomplete steps, missing tests, or incorrect assumptions.
- Emit `<signal>GOAL_COMPLETE</signal>` only when verifiably done.
- Emit `<signal>GOAL_FAILED: <specific reason></signal>` otherwise.

## 4. Reading Critic feedback in logs

```bash
python -m aja tui          # watch critic decisions in the live log panel
python -m aja exec list    # review completed session timelines
```

Critic events appear in the mission journal as `TOOL_CALLED` / `TOOL_COMPLETED`
entries under the `critic` trace ID.

## 5. Calibrating Critic quality

Use the `golden_tasks` LanceDB table to track evaluator accuracy over time:

```python
from aja.memory.secretary import get_aja_memory
from aja.decision.calibration import run_calibration

mem = get_aja_memory()
run_calibration()   # re-runs all golden tasks and updates mismatch_count
```

If `mismatch_rate > 30 %`, the system logs `EVALUATOR_DRIFT_DETECTED`.

## Rules
- Never use the same model for Critic and Worker — identical system prompts defeat the purpose.
- A Critic that always emits `GOAL_COMPLETE` is a silent failure. Monitor `mismatch_count` in `golden_tasks`.
- Keep `max_iterations` low (≤ 8) to surface runaway critic loops quickly.
