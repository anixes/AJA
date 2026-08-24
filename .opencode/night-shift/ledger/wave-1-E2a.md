# Wave 1 — E2a Execution Report

Executor: E2a (T2 fixes F1, F2, F3, F6)
Date: 2026-08-25
Branch state: working tree only — no commits made.

## Files changed

1. `libs/aja-core/aja/llm.py`
2. `libs/aja-core/aja/orchestration/gateway.py` (type guards only; no async changes — A2's territory untouched)
3. `libs/aja-core/aja/orchestration/providers/google_adapter.py`
4. `libs/aja-core/aja/orchestration/providers/openai_compat.py`

## Fixes

### T2#1 (HIGH) — `resolve_provider_model(None)` → `"in None"` TypeError
- **llm.py** (`resolve_provider_model` entry): added falsy guard — `if not model_str: model_str = "google:gemini-2.5-flash"` (the same default `get_gateway()` uses in the same module). Closes all four crash sites (`get_gateway`, `completion`, `completion_async`, `completion_stream`) since raw `json.load` readers pass explicit JSON nulls through as `None` (`.get(key, default)` does not fire for null values).
- Chose normalization at function entry over normalizing the four read sites: single choke point, matches T2 brief proposal C1, and existing pydantic-layer guard (`config.py::_get_default_model`) remains the canonical pattern for the non-raw path.

### T2#2 (HIGH) — Gemini safety-blocked empty candidates IndexError
- **gateway.py** (`_google_generate_content`): replaced `data.get("candidates", [{}])[0]...` with `candidates = data.get("candidates") or []` + conditional index. Empty/absent candidates now log `promptFeedback.blockReason` (warning) and return `None`, matching the function's existing error contract (non-200 path and exception path both return None).
- **google_adapter.py** (`_parse_response`): identical guard; empty candidates yield an empty `LLMResponse(content="")`, matching the adapter's error contract (missing-key case also covered).

### T2#3 (MEDIUM-HIGH) — `response.choices[0]` with empty choices
- **gateway.py** (legacy chat path): guard `if not getattr(response, "choices", None): return None` before indexing — callers' existing None-handling (retry exhaustion contract) engages.
- **openai_compat.py** (`chat`): same guard returns `LLMResponse(content="", model=model)`, matching the adapter's non-retryable-error contract.

### T2#5 / T2-F6 (MEDIUM) — multimodal/tool-role history into Gemini text parts
- **gateway.py**: new module-level `_flatten_google_content()` next to `google_api_key()`. Flattens OpenAI vision-format list content to a single joined text string (text parts joined with `\n`; non-text parts dropped with a debug log since this Gemini path has no image support). `_google_generate_content` history loop now uses it; `role=="tool"` messages are mapped to `"user"` turns prefixed `[Tool result for <tool_call_id>]:` so tool outputs remain visible to the model instead of being collapsed to role `"model"`.
- **google_adapter.py**: mirrored implementation as `GoogleAdapter._flatten_content()` staticmethod + `_convert_messages()` classmethod update (kept symmetric with the gateway legacy path deliberately — no cross-module refactor, per minimal-diff rules).
- Assistant turns carrying `tool_calls` still collapse to model text (pre-existing behavior); full `functionResponse` mapping remains scoped out per council question 3 in the brief.

## Coordination notes
- gateway.py touched ONLY in: module-level helper insertion (after `google_api_key`), the google contents-building block (~line 727), the choices guard (~line 597), and the candidates guard (~line 782). No async lifecycle/session changes — A2-F1/F5 regions untouched.
- Existing `test_role_model_routing.py` (13 tests) re-run green after the llm.py routing change.

## Test results

New file: `tests/python/unit/test_nightshift_wave1_e2a.py` — 14 tests:
- F1: None fallback, empty-string fallback, simulated null-config read, explicit-selection passthrough
- F2: adapter empty candidates / missing candidates key / gateway blocked→None
- F3: openai_compat empty choices / gateway legacy path empty choices→None
- F6: flatten list/multipart/string/None, adapter message conversion (multimodal + tool role), gateway payload capture asserting no list ever reaches a Gemini text part

```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e2a.py -q --timeout=300 -p no:cacheprovider
→ 14 passed in 2.39s

py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e2a.py tests/python/unit -k "gateway or llm or google or provider or completion" -q --timeout=300 -p no:cacheprovider
→ 59 passed, 586 deselected in 10.70s (1 pre-existing unrelated warning)

py -3.12 -m pytest tests/python/unit/test_role_model_routing.py -q
→ 13 passed
```

No regressions observed. All four fixes verified against source before patching; mock payloads match the real-world producers documented in the T2 brief (Gemini `candidates: []` on prompt blocks; Copilot-Claude/llama.cpp `choices: []`).
