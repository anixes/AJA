# Wave 1 — Executor E3 Ledger

**Date**: 2026-08-25
**Scope**: T3#1–T3#5 (peer brief T3.md) + A4#1 (peer brief A4.md)
**Status**: ✅ ALL FIXES APPLIED & VERIFIED — 25 new tests pass; 60/60 regression sweep green.

---

## Fixes

### 1. T3#1 HIGH — FailureMemory writer/reader contract (`failure_memory.py`, `react_executor.py`)
- **Writer** (`react_executor.py:319` in-loop failure record): added
  `"plan_node_ids": [n.id for n in self.graph.primitive_nodes()]` to match the
  escalation writer's shape (:479). Both writers now persist identical key sets.
- **Reader** (`failure_memory.py::get_failure_penalty`): made tolerant —
  `f.get("plan_node_ids") or []`, `f.get("goal_embedding")` None-skip, and the
  `cosine_similarity` call wrapped in `try/except (ValueError, TypeError)` → skip
  (protects against cross-model dimension mismatch per T3 brief).

### 2. T3#4 MEDIUM — `_record_repair` signature drift (`react_executor.py:386`)
- Verified real signature: `PlanStore.record_repair(plan_id, node_id, action, metadata=None)`
  (`plan_store.py:115`). Call site rewritten: `action=rec.action_taken`,
  extras packed into `metadata={"attempt", "failure_kind", "notes"}`.
  Repair telemetry now actually persists to `core_tool_executions` instead of
  dying on TypeError inside the swallow-except.

### 3. T3#2 HIGH — Skill store/executor column contract (`skill_executor.py`, `skill_store.py`)
- **Read sites normalized** (`skill_executor.py`):
  - `execute_skill`: `skill.get("id") or skill.get("skill_id") or "unknown"`.
  - tool_sequence parse accepts BOTH pre-decoded list and JSON string.
- **`recommend_skill()` implemented** in `skill_store.py` (verified genuinely
  absent repo-wide; `skill_composer.py:137` imports it → ImportError at runtime).
  Chose "implement minimal correct version" over rewriting the composer import:
  three files' docstrings contract on it. Includes:
  - `normalize_skill_row()`: `skill_id→id`, decodes `tool_sequence_json→tool_sequence`
    (list), `tags_json→tags`; original keys preserved; corrupt JSON tolerated.
  - `_is_stale_row()`: updated_at-based staleness (30d), `include_stale` filter.
  - `recommend_skill(query_text, min_confidence=0.0, include_stale=False)`:
    semantic search → filters → normalized row or None; search failures log+None.
- Note: `mark_skills_stale` / `touch_skill` imports remain silently-wrapped no-ops
  (out of scope — exception-guarded, non-crashing).

### 4. T3#3 MEDIUM — vector.search null-metadata guard (`memory/vector.py:76`)
- Per-row guard: only parse when `isinstance(raw, str) and raw.strip()`; wrap in
  `try/except (ValueError, TypeError)`; non-dict results coerced to `{}`.
  One null/malformed cell can no longer kill the entire `search()` for all consumers.

### 5. T3#5 MEDIUM — skill_compiler None guards (`cognitive/skill_compiler.py`)
- :99 `(trajectory.domain or "general").lower()` (mirrors existing :112 pattern)
- :132 `str(s.action_payload or "")[:80]`
- :170 `repr(str(step.action_payload or "")[:40])`

### 6. A4#1 CRITICAL — autonomous_loop cancellation-safe teardown (`runtime/autonomous_loop.py`)
- Whole post-lock body wrapped in `try/finally`. On ANY exit path (stop event,
  KeyboardInterrupt, external task cancel → CancelledError is BaseException so
  the old handlers never ran, exceptions):
  1. `heartbeat_task.cancel()` + awaited via `gather(return_exceptions=True)` (suppressed)
  2. `intent_engine.stop()` (guarded, only if started)
  3. `release_lock(lock)` — guaranteed, fixes stale `worker.lock` blocking restart
- Inline cleanup duplicated in the old break branches removed; early-startup failure
  (e.g. LanceRuntimeStore ctor raising) now also releases the lock.

---

## Tests
**File**: `tests/python/unit/test_nightshift_wave1_e3.py` — 25 tests, mock-heavy,
no disk writes outside tmp_path / injected mocks:

| Suite | Covers |
|---|---|
| TestFailureMemoryContract (5) | missing plan_node_ids, None emb, dim-mismatch skip, positive penalty path, writer tripwire |
| TestRecordRepairSignature (2) | exact kwargs sent to PlanStore + live-signature pin |
| TestSkillExecutorNormalization (4) | raw row / normalized row / string seq / empty seq |
| TestRecommendSkill (4) | import resolves, normalize both shapes, corrupt JSON, confidence filter |
| TestVectorSearchMetadataGuard (3) | null cell, malformed cell, all-null table |
| TestSkillCompilerNoneGuards (3) | None domain, None payload slices, explicit-domain tags |
| TestAutonomousLoopCleanup (4) | stop-event exit, external cancel, early-failure release, duplicate-refusal |

## Verification runs
```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e3.py -q --timeout=300 -p no:cacheprovider
→ 25 passed in 6.95s

py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e3.py tests/python/unit \
  -k "failure or skill or vector or react or autonomous or planner" -q --timeout=300 -p no:cacheprovider
→ 60 passed, 669 deselected in 13.18s

py -3.12 -m pytest tests/python/unit/test_serve_entrypoint.py tests/python/unit/test_skill_runtime_guard.py -q
→ 16 passed (serve composition unaffected by autonomous_loop restructure)
```

## Files touched
- libs/aja-core/aja/planning/failure_memory.py
- libs/aja-core/aja/planning/react_executor.py (writer key + _record_repair call only)
- libs/aja-core/aja/skills/skill_executor.py (read sites only)
- libs/aja-core/aja/skills/skill_store.py (+ recommend_skill/normalize_skill_row/_is_stale_row)
- libs/aja-core/aja/memory/vector.py (search() region only)
- libs/aja-core/aja/cognitive/skill_compiler.py (3 None guards only)
- libs/aja-core/aja/runtime/autonomous_loop.py (try/finally + lock release)
- tests/python/unit/test_nightshift_wave1_e3.py (new)

## Notes for next executors / council
- skill_composer.py was NOT edited (its import now resolves); it remains a valid
  consumer of the implemented `recommend_skill`.
- `mark_skills_stale` / `touch_skill` still don't exist in SkillStore (T3 F2 tail);
  their call sites are exception-wrapped so they no-op — cheap follow-up if wanted.
- A4#1 test uses monkeypatched single_instance/intent/goal modules; the real
  serve-level signal path (A4 F5) untouched per scope.
