# Phase 8: Resilient Skill System

The **Resilient Skill System** shifts AgentX from executing one-off tasks to managing a production-grade library of reusable, verifiable, and versioned behaviors. It captures successful tool sequences and transforms them into "skills" that can be safely replayed, chained, and audited.

## Core Objective
Systematize expertise by autonomously capturing successful task execution patterns, validating their correctness via post-execution assertions, and allowing complex multi-skill composition with risk-aware gating.

## Key Components

### 1. Autonomous Skill Capture & Store (`skill_store.py`)
AgentX passively observes successful missions and "crystallizes" them into skills.
- **High-Fidelity Capture**: Tool sequences, sanitized arguments, and success metadata are stored in LanceDB/Arrow.
- **Versioning**: Skills are versioned; structural changes to a tool sequence create a new immutable version for traceability.
- **Recall Engine**: Uses token-matching with synonym expansion (e.g., "fetch" matches "download") and bidirectional scoring to suggest the best skill for an objective.
- **Validity Decay**: Skills track `last_used_at` and `success_count`. Confidence scores decay if a skill hasn't been used for 30 days or starts failing postconditions.

### 2. Risk-Aware Safe Execution (`skill_executor.py`)
Executing a skill is more than just replaying tools; it requires environment safety.
- **Environment Validators**: A pluggable registry checks prerequisites (e.g., "Is Docker running?", "Is the database reachable?") before a single tool is invoked.
- **Step-Level Recovery**: Checkpoints are stored for every step. If a skill execution is interrupted, it can resume precisely where it left off, skipping already-completed tools.
- **Risk Gating**: Skills are tagged with risk levels (LOW, MEDIUM, HIGH). HIGH-risk skills require a single unified operator confirmation before the chain begins.

### 3. Postcondition Verification (`skill_postconditions.py`)
Execution success is no longer defined by "zero exit codes," but by **semantic correctness**.
- **Assertion Registry**: Skills can define mandatory post-execution checks:
  - `key_present`: Verify a specific field exists in a tool's JSON output.
  - `value_equals`: Ensure a result matches an expected state.
  - `file_exists` / `row_count_gte`: Validate file-system or database side-effects.
- **Confidence Feedback**: Failing a `required` postcondition triggers a `SKILL_FALLBACK` and decrements the skill's confidence score, preventing the system from over-relying on "broken" patterns.

### 4. Multi-Skill Composition (`skill_composer.py`)
Complex missions are decomposed into chains of specialized skills.
- **Heuristic Splitting**: Automatically detects compound objectives (e.g., "fetch data *then* process it") and maps them to a sequential skill chain.
- **Context Injection**: Uses template-string substitution (`{{variable}}`) to pass outputs from one skill (e.g., a `user_id`) into the input arguments of the next skill in the chain.
- **Unified Gateway**: The composer re-evaluates the combined risk of the entire chain to ensure the operator only has to approve once.

### 5. Introspection & Ambiguity Resolution (`skill_introspect.py`)
Transparency and explainability for autonomous selection.
- **Explainability Interface**: Generates markdown summaries of skills, including tool shapes, success rates, risk levels, and version history.
- **Ambiguity Gating**: If two skills match a query with near-identical scores (within 5%), the system suspends execution and requests manual resolution from the operator.

## Interfaces

### CLI / Runtime
- **Discovery**: `agentx` now uses `recommend_skill()` before attempting a raw task plan.
- **Explain**: Users can inspect any recommended skill before allowing it to run.
- **Feedback**: Post-execution reports now include a "Correctness" section based on postconditions.

### Architecture Insight
> **Task** = Ephemeral intent  
> **Skill** = Proven, versioned, and verifiable pattern  
> **Composition** = Strategic chaining of proven patterns  

## Validation Invariants

| Invariant | Guarantee | Status |
| :--- | :--- | :--- |
| **1. Atomic Resume** | Interrupted skills resume from the last successful checkpoint. | ✅ **VERIFIED** |
| **2. Risk Isolation** | High-risk skills never execute without a prior approval lock. | ✅ **VERIFIED** |
| **3. Semantic Correctness** | Skills that pass tools but fail assertions are marked as "Broken". | ✅ **VERIFIED** |
| **4. Context Integrity** | Chained skills correctly inject variables from prior results. | ✅ **VERIFIED** |
| **5. Version Safety** | Replaying a skill uses the exact tool sequence from its version record. | ✅ **VERIFIED** |

### Test Harness
A Phase 8 smoke test is available in `.agentx/test_phase9.py` (which covers these gaps). It verifies postcondition failures, context injection, and ambiguity resolution.
