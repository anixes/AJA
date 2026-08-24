# Wave 1 — E5 Execution Report

Executor: E5 · Date: 2026-08-25
Scope: A4#2, A4#3, A4#5, A4#4 (peer briefs: A4 primary, A2 secondary)

## Fixes applied

### 1. A4#2 HIGH — `goals/goal_engine.py` sync planning froze the loop
- `_step_planning` now runs `await asyncio.to_thread(self.expand_goal, goal)` instead of the inline sync call (which bottoms out in `planner.decompose → run_async_synchronously` thread-join on-loop).
- Result handling unchanged: same try/except → `PLAN_CREATED` bus publish + `record_scheduler_event` (already to_thread'd) on success, `FAILED` + save_state on error.
- Note: `llm.completion_async` exists but is a raw prompt-completion helper — wiring it into `planner.decompose` would be a planner refactor, not a minimal diff. Chose the to_thread wrap (also A4's proposed remediation). Longer-term async decompose remains deferred.

### 2. A4#3 HIGH — `scheduler/cron_scheduler.py` tick-loop blocking IO
All inside `tick_loop`; cron math untouched:
- `store.list_tasks(status="scheduled", limit=10000)` → `await asyncio.to_thread(...)`
- All three in-tick `store.update_task(...)` calls (invalid one-shot disable, reminder cleanup/archive, due-job last_run/active_run write) → to_thread.
- New `CronScheduler._emit_event_async(...)` wraps `_emit_event` in to_thread; used for the two tick-context emissions (`SCHEDULER_JOB_SKIPPED_OVERLAP`, `SCHEDULER_JOB_DUE`). Sync `_emit_event` kept intact for all existing callers/tests (cron job API unbroken).
- Deferred (not in scope): `_execute_job`'s `_read_job_meta`/`_mutate_job_meta`/`_deliver_research_report` still do sync store IO; `_mutate_job_meta`'s 50ms retry sleep (A4 F4); briefing compose (A4 F3-related); startup projection rebuild (F8).

### 3. A4#5 MEDIUM — `runtime/events.py` + `autonomy/intent_engine.py`
- `events.py`: added `LanceRuntimeEventSink.emit_async()` (to_thread offload of the sync protocol method) and a lock-guarded module-level `get_shared_runtime_sink()` singleton. Verified trivially safe: underlying `AJAMemory` is already a process-wide singleton (`secretary.get_aja_memory`), so sharing one adapter instance changes nothing about write concurrency. The sync `RuntimeEventSink` protocol is intentionally unchanged.
- `intent_engine.py`: `loop()` now runs `await asyncio.to_thread(self.execute, intent)` — this offloads the whole blocking body (LanceDB mission write via add_goal, experience save, cooldown file write, report sink emit) off-loop. Unsafe-intent escalation `_send_telegram_report(...)` also wrapped in to_thread.
- Not touched (out of allowed files): `scheduler/telegram.py:84` constructs `LanceRuntimeEventSink()` per report — with the shared-memory reality this costs only adapter construction, not a DB open. Flag for whoever owns telegram.py.

### 4. A4#4 MEDIUM — `runtime/serve.py` signal hardening
- `_handle()` now sets the stop event exclusively via `loop.call_soon_threadsafe(stop_event.set)`.
- POSIX path keeps `loop.add_signal_handler`; Windows fallback keeps `signal.signal` registration (NotImplementedError branch unchanged).
- Docstring documents the Windows caveat: external SIGTERM is undeliverable on win32; graceful teardown relies on Ctrl+C/SIGBREAK or stop-event plumbing (healthcheck / side channel), and `docker stop` on Windows containers will not reach these handlers.

## Tests

New file: `tests/python/unit/test_nightshift_wave1_e5.py` (9 tests, anyio-marked):
- step_planning runs expand_goal off-thread + loop stays responsive during 250ms blocking plan; planner failure still marks FAILED
- tick_loop list_tasks runs off-loop with loop-heartbeat assertion under 150ms blocking reads; SKIPPED_OVERLAP emit off-loop
- emit_async returns event id from worker thread; get_shared_runtime_sink is a singleton
- intent loop executes intents off-loop (thread-identity assertion through real `loop()`)
- serve fallback installs both handlers and routes stop via call_soon_threadsafe (spy on real loop); POSIX-preferred path test (fake add_signal_handler, no signal.signal fallback)

Results:
```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e5.py -q --timeout=300
→ 9 passed

py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e5.py tests/python/unit \
  -k "cron or scheduler or goal or serve or intent" -q --timeout=300 -p no:cacheprovider
→ 63 passed, 1 skipped (pre-existing live-key skip), incl. test_serve_entrypoint,
   test_scheduler_bugs, test_briefing, test_reminders, test_scheduled_research
```
Note: one run appeared to hang at ~305s — traced to pre-existing `test_secretary_fixes.py::test_heartbeat_preserves_worker_profile` setup (290s LanceDB init, unrelated to these changes); rerun completed clean.

## Deferred items
1. Async-native `decompose` so `run_async_synchronously` is never invoked at all (A2-F4/A4-F2 longer-term).
2. `_execute_job` internals: `_read_job_meta`, `_mutate_job_meta` (+ its 50ms sleep, A4 F4), `_deliver_research_report` sync IO.
3. Briefing compose + startup projection rebuild offload (A4 F3/F8).
4. `telegram.py` fresh-sink construction — file outside E5's allowed set; shared sink accessor now available (`events.get_shared_runtime_sink`) for that owner.
5. EventBus sync-publish dropping awaitables (A4 F10) — behavior change needs council decision.
