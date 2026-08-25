# Wave 2 — E5 Recovery Report (LLM core semantics)

**Executor**: E5 (finish-don't-redo recovery of dead agent's partial work)
**Date**: 2026-08-25
**Claim honored (exclusive)**: `orchestration/gateway.py` (retry/timeout/contract regions only; Wave-0 image-routing block untouched), `orchestration/providers/openai_compat.py` (retry/timeout config only), `llm.py` (return-contract only), `utils/self_healer.py`, `decision/engine.py`.

---

## STEP 1 inventory — prior agent's state

The dead agent had completed nearly everything. Found **done** on arrival:

- F1: `_llm_timeout_s()` (`AJA_LLM_TIMEOUT_S`, default 120s, ValueError-safe) + `_backoff_sleep_seconds`/`_backoff_sleep` helpers in both gateway.py and openai_compat.py.
- F1 applied: gateway `_get_openai_client` (`timeout=`, `max_retries=0`) and `_get_session` (`ClientTimeout(total=_llm_timeout_s())`, was hard-coded 60); openai_compat `_get_client`.
- F3: both retry loops use jittered `_backoff_sleep(attempt)` instead of bare `asyncio.sleep(2**attempt)`.
- F4: Responses-path ValueError now carries `.status_code = resp.status` → legacy-loop classifier (:716-724) fast-fails deterministic 4xx.
- F7: all three chat paths return None + `logger.warning` on empty content (adapter :398, Responses :641, legacy :706); llm.py `completion`/`completion_async` annotated `Optional[str]`, `or ""` coercions removed, registered-provider empty-content paths log warnings; `_choices_from_chat_result` helper logs and shapes None safely.
- F5: self_healer uses `run_async_synchronously(gateway.chat(...))` + falsy guard before `write_text` (no file destruction).
- F6: decision/engine awaits via `run_async_synchronously` + explicit no-response branch returning visible NEW/confidence-0 ("No response from LLM gateway.") — not masked as parse error.
- Test file present, unverified.

**Missing / fixed by E5**:

| Gap | Fix |
|---|---|
| F1 leftover: `embed()` at gateway.py:956 built a bare `AsyncOpenAI(...)` (SDK 600s default + internal retries still active) | Added `timeout=_llm_timeout_s(), max_retries=0` — last AsyncOpenAI construction in claimed files now bounded |

That was the only edit required. No other silent→"" conversion sites remain in claimed files (swept via rg).

## STEP 2 brief compliance notes

- L2-F9 (401 refresh hot-loop twins) intentionally NOT touched: outside mission scope list (F1-F4/F5-F6/F7 + sweep), touches shared retry regions other waves may claim.
- L2-F10 (stream fallback duplication) out of claim.
- L1-F3 timeouts section satisfied incl. env knob naming `AJA_LLM_TIMEOUT_S` default 120.

## Test results

Load-cap command (per rules):

```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave2_e5.py tests/python/unit -k "gateway or llm or heal or decision or timeout or retry" -q -n 2 --timeout=300 -p no:cacheprovider
```

→ **96 passed, 12 failed — ALL 12 failures are OUTSIDE this executor's exclusive claim**:

- `test_nightshift_wave2_e2.py` ×11 (E2's files: `api/bridge.py` — missing `AJAMemory.get_runtime_events` at bridge.py:1701, stale `FakeApprovalMemory` fixture vs new `get_active_approval`; `utils/redact.py` — telegram bot-token pattern not redacted). Owned by E2.
- `test_nightshift_wave1_e2b.py::test_native_registry_execute_async_matches_execute` ×1. Owned by wave-1 e2b owner (`orchestration/tools/native.py`).

Per exclusive claims, E5 did NOT touch those files.

**E5-owned verification**:

```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave2_e5.py -q -n 2 --timeout=300 -p no:cacheprovider
→ 19 passed in 16.40s  ✅ GREEN
```

Covers: env-knob parsing (default/garbage/float), OpenAI client timeout+max_retries=0 (gateway & adapter), aiohttp session ClientTimeout, jitter bounds [0.5x,1.0x]×min(30,2^n), Copilot Responses 400 single-attempt fast-fail, status-carrying fast-fail in legacy loop, statusless retry burn with backoff attempts [1,2], None-vs-str contract on all three chat paths + completion_async passthrough, self_healer awaited-write/no-truncate-on-failure/coroutine-guard, engine None-fallback visibility/parse/coroutine-await.

## Handoff

- For E2: bridge.py:1701 calls `mem.get_runtime_events(50)` which doesn't exist on AJAMemory (has `add_runtime_event`); redact.py needs a telegram bot-token pattern (`\d{10}:[A-Za-z0-9_-]{35}`).
- For wave-1 e2b owner: native registry execute-async parity failure pre-existing in working tree.
