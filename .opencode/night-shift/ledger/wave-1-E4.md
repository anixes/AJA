# Wave 1 — E4 Execution Ledger

**Executor**: E4 · **Date**: 2026-08-25 · **Branch**: `native-worker-3`
**Primaries**: T4 (type-contract hunt), A3 (async/event-loop audit)

## Fixes Applied

### 1. T4#1 HIGH — tui.py: un-awaited async gateway.chat
- `libs/aja-core/aja/interface/tui.py` (`AJAShell.on_input_submitted`)
- Verified `LLMGateway.chat` is `async def` (orchestration/gateway.py:293). Replaced
  `loop.run_in_executor(None, self.gateway.chat, ...)` with a direct
  `response = await self.gateway.chat(self.model, prompt)` on the event loop
  (handler was already async). Added `if not isinstance(response, str): response = ""`
  so the legacy None-on-failure return can't hit `"```" in response`.
- Also removed the deprecated `asyncio.get_event_loop()` call at the same site.

### 2. T4#2 MEDIUM-HIGH — intent_parser.py: non-dict LLM JSON crash
- `libs/aja-core/aja/interface/intent_parser.py` (both `parse_intent` and `parse_intent_async`)
- After fence-stripping + `json.loads`, added:
  `if not isinstance(data, dict): raise ValueError(...)` → falls into each function's
  existing `except` → question fallback dict (preserves the established fallback contract;
  no consumer sees a list/scalar reaching `.get()`).

### 3. T4#3 MEDIUM — content=None → Delta/Final(text=None)
- `libs/aja-core/aja/core/conversation.py:497`: `m.get("content", "")` → `m.get("content") or ""`
  (key-exists-with-null case). Prevents `Final(text=None)` from the history-scan path.
- `libs/aja-core/aja/interface/renderers.py:147`: `render_delta(event.text or "")`
  (mirrors repl.render_final hardening; deliberately did NOT touch `final_text = event.text`
  at line 161 — None there is the "no Final seen" sentinel).
- `libs/aja-core/aja/tui/dashboard.py:537`: `_delta_buffer.append(ev.text or "")`.

### 4. A3#2 MEDIUM — dashboard.py _safe_provider blocked the Textual loop
- Sync providers now run via `await asyncio.to_thread(fn)`; coroutine providers still
  awaited directly (`asyncio.iscoroutinefunction(fn)` branch). Error-degradation dict unchanged.

### 5. A3#1 CRITICAL — tui.py blocking subprocess.run(shell=True) on UI loop
- `audit_and_execute` is now `async`; subprocess moved to
  `asyncio.wait_for(asyncio.to_thread(subprocess.run, ...), timeout=self.SHELL_TIMEOUT_S)`
  with `SHELL_TIMEOUT_S = 60` (class attr, patchable) and an explicit timeout log message.
  Both call sites updated to `await`. Deny path untouched.

## Tests

New file: `tests/python/unit/test_nightshift_wave1_e4.py` — **19 passed** (~9s, mock-heavy,
no real TUI launches; dashboard tests use Textual headless `run_test()` per house style).

Coverage map:
- chat awaited / non-str response tolerated / `<cmd>` routes into awaited execute (T4#1)
- subprocess off-loop thread verified + 2s sleep cut at 0.2s timeout; success path; deny short-circuit (A3#1)
- parse_intent & parse_intent_async: `[1,2,3]`, `"str"`, `42`, `null` → question fallback; valid dicts pass through (T4#2)
- conversation content=None → `Final.text == ""`; renderer Delta(None) → ""; dashboard Delta(None) buffer == [""] (T4#3)
- `_safe_provider`: sync fn proven to run in worker thread ≠ loop thread; async fn supported; error degrades (A3#2)

Command run (as instructed):
```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e4.py tests/python/unit -k "tui or intent or conversation or renderer or dashboard" -q --timeout=300 -p no:cacheprovider
```
**Result**: 92 passed, 1 skipped, 4 failed — all 4 failures are NOT from E4:

| Failing test | Cause | Evidence |
|---|---|---|
| 3–4 × `test_dashboard_v2.py::…` (rotating set each run) | PRE-EXISTING flake: `_tick_spinner` (dashboard.py:588) races screen mount/teardown → `NoMatches: '#status-line'` | Stashed E4 diffs and re-ran baseline 3×: `14 passed`, `1 failed`, `2 failed` — fails identically WITHOUT E4 changes |
| `test_nightshift_wave1_e5.py::test_intent_engine_loop_executes_intents_off_loop` | E5's own WIP test file vs E5's in-flight `intent_engine.py` edits (`FakeGoalEngine.goals` attr mismatch) | File owned by E5; not in E4 scope |

## Deferred items
1. **Pre-existing `_tick_spinner` flake (dashboard.py:588)** — one-line defensive guard around
   `query_one("#status-line", Static)` would stabilize it, but it sits outside this wave's
   approved diff scope ("render_event guard + _safe_provider off-loop only"). Recommend a
   fast-follow; it will keep polluting CI signal randomly (~1-2 tests/run).
2. T4 council Q1/A3 C-Q2: whether legacy `AJAShell` surface should be deleted rather than
   hardened — out of scope; fixes applied as instructed.
3. T4#2 deeper coercion (non-str `command` passing truthy guard, missing `task` key for cron
   docstring wiring) noted by T4 but beyond "shape validator" mandate.
4. T4#4 (InboundMessage `__post_init__` coercion) and T4#5 (tokenizer_map non-str) — not in E4 fix list.

## Files touched
- libs/aja-core/aja/interface/tui.py
- libs/aja-core/aja/interface/intent_parser.py
- libs/aja-core/aja/core/conversation.py
- libs/aja-core/aja/interface/renderers.py
- libs/aja-core/aja/tui/dashboard.py
- tests/python/unit/test_nightshift_wave1_e4.py (new)
