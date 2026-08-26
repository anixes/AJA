# Overnight Session — 2026-08-26 (Zed agent, ox-alpha)

Continuation of the night-shift campaign. Context recovered from OpenCode
session DB; work executed via parallel research/testing agents + direct
implementation.

## Commits (in order)

1. `8183334` refactor(gateway): replace adapter-swap lock with ContextVar responder
   - Finished the E1-deferred gateway_runner refactor: process_event sets
     `_current_responder` ContextVar instead of swapping telegram_adapter under
     a lock. AdapterProxy + _event_lock removed. Concurrent platform pollers
     no longer serialize. Includes wave3 LanceDB empty-schema self-heal
     (secretary.py) from the prior session's subagent.
2. `e6c401f` test(planning): harden async decompose test isolation and contracts
   - New tests/python/planning/test_async_decompose.py (5 tests, TDD for
     decompose_async). Fixed: stub full LLM surface in sync regression guard
     (real gateway client was leaking httpx2 cleanup crashes into later anyio
     task groups). Updated wave1-E5 step_planning test to the async-native
     contract (zero thread spawns, awaited expand_goal_async).
3. `abc153b` refactor(scheduler): offload _execute_job sync IO off the event loop
   - _read_job_meta, research report delivery, finalize meta mutation (+ its
     50ms retry sleep), briefing compose, _clear_run_metadata all now run via
     asyncio.to_thread. Sync helpers kept for sync callers.
4. `37ec04b` docs: Telegram UX upgrade plan (docs/plans/TELEGRAM_UX_UPGRADE.md)
   - Researched Hermes Agent + OpenClaw Telegram integrations. Gap analysis +
     tiered build plan. Tier 1 next session: ack reactions, tool-progress
     status bubble, setMyCommands menu, notification discipline.
5. `9a7a646` feat(planner): async-native decompose path
   - The prior session's implementation (861 lines across planner/generator/
     verifier/simulation/goal_engine), validated this session: decompose_async
     + all mirrors; _step_planning awaits expand_goal_async directly.

## Validation

- tests/python/planning: 97 passed (baseline 92)
- test_async_decompose.py: 5/5 passed
- Unit slice "goal or cron or scheduler or intent": 50 passed, 1 skipped
- test_nightshift_wave1_e5.py: 9/9 passed
- Combined regression sweep: 114 passed

## Environment notes

- venv was missing pytest-timeout/pytest-xdist (installed). The 11 unit files
  with f-string-backslash SyntaxErrors still require py3.12+ to collect —
  pre-existing, unrelated.
- Subagent spawning failed repeatedly ("No response from subagent") mid-
  session; work continued directly. Static-review agent DID deliver the key
  finding that the TDD fake gateway was never exercised.

## Remaining backlog (for next sessions)

### READY TO IMPLEMENT (full specs in docs/plans/)
1. Telegram Tier 1 UX (TELEGRAM_UX_UPGRADE.md): ack reactions, tool-progress
   status bubble edited in place, setMyCommands registration, disable_notification.
2. Wire run_direct_loop agentic harness into /pc path (multi-step tool loops).
3. Per-command exec approvals (EXEC_APPROVALS_SPEC.md) — full spec ready, ~1 day.
4. P4 bare-except sweep (P4_BARE_EXCEPT_TRIAGE.md) — 15 C-items with fix
   sketches, ~12 B, ~18 A; work order: skills+decision C first. NOTE: fix
   _skill_done read-path BEFORE _log_skill_status write-path logging.
5. Bridge split phase 3 (BRIDGE_SPLIT_PHASE3.md) — AppContext + config_store
   extraction is a safe half-day first step; read test_runtime_boundaries.py
   AST checks before restructuring.
6. agent_memory table drop (~1h, trivial — see ARCH_DEBT_DECISIONS.md).
7. Episodic memory vector index via VectorMemory(table="aja_episodes")
   (ARCH_DEBT_DECISIONS.md item 1, 0.5-1 day). Ledger was half-stale: chat
   recall already vector; only cognitive episodes + RAM-only ExperienceStore weak.

### NEEDS OWNER DECISION
- Jobs/tasks table split (2-3 days, behavioral change) vs ADR-only — see
  ARCH_DEBT_DECISIONS.md item 2.
- Fund experience-store persistence or accept keyword recall.
- EventBus F10 sync-publish behavior change.

### Untracked files pending decision .opencode/night-shift/agy/,
   wave3-repair.md ledger, test_nightshift_wave3_repair.py (all pass; commit
   recommended).

## Standing rules honored

- pytest -n 2 max (RAM limits); failures.json checked out before commits;
  live gateway/worker never restarted.
