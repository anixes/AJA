# NIGHT-SHIFT WAVE 1 — E2b Execution Ledger

Executor: E2b · Date: 2026-08-25 · Branch state: shared working tree with peers (E1/E2a/E3/E4/E5 edits in flight)

---

## Fixes landed

### Fix 1 — A1#1 CRITICAL: sync LLM roundtrip on the gateway loop (`/pc` path)
- **File**: `libs/aja-core/aja/gateway/remote_control.py:5,29`
- **Verified first**: `parse_intent_async` exists at `interface/intent_parser.py:462`; signature
  `(message, history, system_state=None)` and output contract (type/goal/command/tool_calls/response/confidence
  + identical exception fallback dict) match `parse_intent` exactly.
- **Change**: import + call swapped to `await parse_intent_async(...)`.

### Fix 2 — A1#2/A2#2 CRITICAL/HIGH: blocking tool execution on the loop
- **Files**: `orchestration/tools/executor.py`, `orchestration/tools/native.py`
- **Change**:
  - `executor.py`: extracted `_check_permission` / `_resolve_cwd` / `_result_to_dict` helpers
    (sync `execute()` behavior byte-identical) and added **async-native `execute_async()`**
    which awaits `ExecutionManager.run(ExecutionRequest(...))` directly — no thread hop, no
    `thread.join()`, loop stays live for the full command duration.
  - `native.py`: added **async-native `execute_async(name, arguments)`** =
    `await asyncio.to_thread(self.execute, ...)` (tool bodies are sync subprocess/urllib;
    result contract unchanged).
- **Orchestrator note (E1 coordination)**: orchestrator.py L236-239 still calls the sync
  methods inline. Per instructions I did NOT touch orchestrator.py. The async entry points are
  ready; wiring is either `await asyncio.to_thread(tool_registry.execute, fn_name, fn_args)`
  at L236-239 or `await tool_registry.execute_async(fn_name, fn_args)` — one line each, E1's call.
- **Deferred with reason**: `activity_rt.py:288` (`_run_python` → `registry.execute`) has the same
  hazard but activity_rt.py was NOT in my allowed-file list. One-line fix when someone claims it:
  `result = await asyncio.to_thread(registry.execute, activity.tool, activity.args)` (keep semaphore).

### Fix 3 — A2#3 HIGH: `dispatch_worker` runs subprocess adapters on the loop
- **File**: `orchestration/adapters.py:3,43-45`
- **Change**: `return await asyncio.to_thread(adapter.run, baton, workspace_dir)` (+ asyncio import,
  explanatory comment). Native-worker path (`run_async`) untouched.

### Fix 4 — A2#4 HIGH: sync-over-async bridge at direct-loop command site
- **Traceback verification**: `run_direct_loop` is `async def`; every caller awaits it on a running
  loop (SwarmEngine.execute_direct → gateway chat). So `executor.execute(cmd)` always takes the
  running-loop branch of `_run_execution` (executor.py thread-spawn + `thread.join()`) → blocks the
  loop up to 30s/command. This call site IS on the loop → fixed.
- Sync-only legacy callers outside any loop take the `asyncio.run` branch — acceptable, untouched.
- **Change**: `direct_loop.py:270` now prefers `await executor.execute_async(cmd)` and falls back to
  `await asyncio.to_thread(executor.execute, cmd)` for duck-typed/sync-only injected executors.
  Module stays stdlib-pure (asyncio import is local + stdlib).

### Fix 5 — A2#1 CRITICAL: per-call provider adapter leak in gateway.chat()
- **File**: `orchestration/gateway.py` — restricted to the adapter-path block (~L311-340); nothing
  else in gateway.py touched (shared with E2a).
- **Verified first**: all four adapters expose `async close()` per the ProviderAdapter protocol
  (`providers/base.py:67`; openai_compat:303, google_adapter:215, anthropic_adapter:53).
- **Change**: adapter usage wrapped in try/finally; finally awaits `adapter.close()` guarded by
  getattr + swallowed-close-exception (logged debug). Exceptions from `chat()` still propagate to
  the existing outer handler → legacy fallback semantics unchanged; close now always runs first.

### Fix 6 — llm.py `run_async_synchronously` hardening ONLY (rest of llm.py = E2a)
- **File**: `aja/llm.py:150-176` region only.
- **Hazards fixed**: (a) worker-thread crash before future resolution deadlocked `.result()`
  forever → now raises RuntimeError if the future never resolves; (b) `except Exception` missed
  BaseException (CancelledError is BaseException on 3.12) → same deadlock → caught and propagated;
  (c) `loop.close()` UnboundLocalError masked the original error when `new_event_loop()` failed →
  loop reference guarded; coro closed when never awaited. Diff confined to this function.

---

## Tests

New: `tests/python/unit/test_nightshift_wave1_e2b.py` — 16 tests, all passing:
- remote_control uses async parser (sync parser asserted NOT called); tool-calls path intact.
- ToolExecutor.execute_async native await path + deny-list respected.
- NativeToolRegistry.execute_async parity + non-blocking proof (heartbeat progresses during a 0.5s sleep tool).
- dispatch_worker offloads sync adapter (thread identity + loop-liveness assertions); native run_async path intact.
- run_direct_loop prefers execute_async (0 sync calls); sync-only executor falls back to to_thread (off-loop thread proven).
- gateway.chat closes adapter per call (×2 calls → ×2 closes), incl. on adapter failure.
- run_async_synchronously: plain loop, in-loop bridge, BaseException propagation, dead-worker no-hang.

Updated: `tests/python/unit/test_telegram_remote_control.py` — patched symbol renamed
(`parse_intent` → async twin wrapper), assertion logic unchanged.

### Command results

```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e2b.py -q --timeout=120
→ 16 passed in 5.68s

py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e2b.py tests/python/unit \
  -k "remote or adapter or executor or tool or direct" -q --timeout=300 -p no:cacheprovider
→ 86 passed, 2 failed (see triage below)
```

### Failure triage (neither is an E2b regression)
1. `test_nightshift_wave1_e1.py::test_runner_routes_each_event_through_its_own_adapter`
   — KeyError in `GatewayRunner.process_event` routing map. `gateway_runner.py` is modified in the
   working tree by E1 and this is E1's brand-new test; fails identically without any of my files
   involved. Owner: E1.
2. `test_dynamic_workspace_security.py::test_tool_executor_dynamic_cwd_resolution`
   — flaked once with "Command timed out after 30s" after 235s wall time while peer agents ran
   concurrent suites; passes cleanly in isolation (3.19s). The sync `execute()` code path it
   exercises is behaviorally unchanged (helpers extracted, same logic). Re-run green.

---

## Deferred / handoff notes
- `activity_rt.py:288` to_thread wrap — out of my file list (see Fix 2 note).
- Orchestrator L236-239 wiring — E1 owns; both `to_thread` and `execute_async` options are drop-in.
- `_run_tests` in adapters.py still has NO timeout (A2#3 second half) — timeout addition touches
  many adapter methods beyond the single call site I was scoped to; recommend Wave-2.
