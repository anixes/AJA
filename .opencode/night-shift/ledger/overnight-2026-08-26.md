# Overnight Session — 2026-08-26 → 2026-08-27 (Zed agent, ox-alpha)

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
     tiered build plan.
5. `9a7a646` feat(planner): async-native decompose path
   - The prior session's implementation (861 lines across planner/generator/
     verifier/simulation/goal_engine), validated this session: decompose_async
     + all mirrors; _step_planning awaits expand_goal_async directly.
6. `5edc57b` docs: research specs — EXEC_APPROVALS_SPEC, P4_BARE_EXCEPT_TRIAGE,
   BRIDGE_SPLIT_PHASE3, ARCH_DEBT_DECISIONS + overnight ledger
7. `cecb9d1` refactor(memory): require explicit VectorMemory table_name
   - agent_memory table confirmed orphaned; reindex tuple cleaned.
8. `787309f` feat(telegram): Tier 1 UX — ack reactions 👀→✅, StatusBubble,
   setMyCommands menu, notification discipline. Wired into orchestrator.
9. `af7c972` test: memory guard plugin + singleton reset fixtures
10. `5a74007` fix(p4): bare-except C-items decision/ + skills/ (with new test file)
11. `1d9eeb8` refactor(api): AppContext + config_store extraction (bridge P2 3.1)
12. `87065e0` fix(p4): bare-except C-items runtime/ (broadcast, rehydrator,
    manager orphan-detection + crashed-marker logging)
13. `ea7f982` feat(gateway): per-command exec approvals with inline buttons
    - PendingCommandStore (one-shot TTL tokens, per-token locks, EXEC_* journal)
      + tg_client execok_/execno_ callback routing behind owner allowlist.
14. `06f85cd` feat(telegram): Tier 2 — MEDIA file delivery + error policy
    - reply_extras.py: MEDIA: tag extraction (dup collapse, missing-file
      warnings), ErrorPolicy (always/once/silent with dedupe),
      format_error_reply, send_documents with size cap. tg_client.send_message
      strips tags and delivers documents after text.

## Validation

- tests/python/planning: 97 passed (baseline 92)
- test_async_decompose.py: 5/5 passed
- Unit slice "goal or cron or scheduler or intent": 50 passed, 1 skipped
- test_nightshift_wave1_e5.py: 9/9 passed
- Combined regression sweep: 114 passed
- Full serial sweep (planning+unit, minus py3.12-only files): 891 passed,
  3 skipped; the only 6 failures are PRE-EXISTING (5 = temporal_graph.py:314
  f-string SyntaxError on py3.11 — needs py3.12; 1 = test_default_is_persona
  ordering flake, passes in isolation). Verified identical on clean HEAD.

## CRITICAL FINDING: aja_runtime_events table bloat (see MEMORY_LEAK_FINDINGS.md)

The production LanceDB table reached **16,977 fragments / 5,641 versions /
7 GB** because tracker.log_event() writes every plan-node trace to the REAL
DATA_DIR during serial test runs (conftest only isolates AJA_DATA_DIR under
xdist workers). This caused:
- The `test_stress_large_random_wave` hang (main thread blocked in
  lancedb background_loop future.result() on table.add)
- Major contributor to the 18-22GB python.exe memory events → dxgmms2 bugchecks

**REMEDIATED**: compact_files + optimize + cleanup_old_versions ran;
table now 23 MB. Root causes still to fix:
1. conftest must isolate AJA_DATA_DIR for ALL runs, not just xdist
2. tracker.log_event should no-op under pytest
3. Add periodic retention/compaction job for aja_runtime_events

## Environment notes

- venv was missing pytest-timeout/pytest-xdist (installed). The 11 unit files
  with f-string-backslash SyntaxErrors still require py3.12+ to collect —
  pre-existing, unrelated.
- Subagent spawning failed repeatedly ("No response from subagent") mid-
  session; work continued directly. Static-review agent DID deliver the key
  finding that the TDD fake gateway was never exercised.

## Remaining backlog (for next sessions)

### READY TO IMPLEMENT (full specs in docs/plans/)
1. ~~Telegram Tier 1 UX~~ → **DONE** (commit 787309f): ack reactions 👀→✅, StatusBubble, setMyCommands menu, notification discipline. StatusBubble wired into orchestrator.handle_gateway_event; reply_to_message_id passed at final send.
2. Wire run_direct_loop agentic harness into /pc path (multi-step tool loops).
3. ~~Per-command exec approvals~~ → **MOSTLY DONE** (commit ea7f982): PendingCommandStore + tg_client callback routing for execok_/execno_. REMAINING: wire the /pc shell path to actually consult the store + CommandGuard re-classification at execution time (the TOCTOU contract is documented in EXEC_APPROVALS_SPEC.md but not yet enforced at a call site — no /exec intent exists yet).
4. ~~P4 bare-except C-items decision/skills/runtime~~ → **DONE** (commits 5a74007, 87065e0). Remaining: B-items (debug logging) and A-items (comments) per P4_BARE_EXCEPT_TRIAGE.md.
5. ~~Bridge split phase 3.1~~ → **DONE** (commit 1d9eeb8): AppContext + config_store extracted; bridge constants are context aliases. Next: approval_service extraction, then telegram_gateway.
6. ~~agent_memory table drop~~ → **DONE** (commit cecb9d1): VectorMemory.table_name now required; reindex tuple cleaned.
7. Episodic memory vector index via VectorMemory(table="aja_episodes")
   (ARCH_DEBT_DECISIONS.md item 1, 0.5-1 day). Ledger was half-stale: chat
   recall already vector; only cognitive episodes + RAM-only ExperienceStore weak.
8. ~~Telegram Tier 2: MEDIA delivery + error policy~~ → **DONE** (commit 06f85cd): reply_extras.py + send_message integration; ErrorPolicy class exists but is NOT yet instantiated in the orchestrator error paths — wire it where handle_gateway_event catches exceptions.

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
