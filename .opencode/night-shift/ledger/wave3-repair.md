# Wave 3 Follow-Up: LanceDB Crash-Corruption Self-Healing

**Date**: 2026-08-25 · **Executor**: night-shift wave 3 follow-up
**Incident class**: hard power-cut during table write tore `aja_missions` metadata → table listed but zero-field schema → every query threw `LanceError(Schema): No field named status` → autonomous worker error-looped forever.

---

## What Was Implemented

### 1. `libs/aja-core/aja/memory/secretary.py` (only file touched)

- **`AJAMemory.KNOWN_TABLE_SCHEMAS`** — name→schema map of the 9 owned tables (`aja_tasks`, `aja_communications`, `aja_approvals`, `aja_workers`, `aja_worker_executions`, `aja_runtime_events`, `aja_territory_knowledge`, `aja_missions`, `aja_chat_history`). Documentation only; never used to alter healthy tables.
- **`AJAMemory._repair_empty_schema_tables()`** — called from `_init_tables()` AFTER the create-missing block and BEFORE the schema-evolution `add_columns` loop (so evolution never touches a broken table).
  - Iterates KNOWN tables present in the DB; opens each defensively.
  - **Zero-field schema = corruption signature** → drop + recreate with the canonical schema constant; logs `warning: repaired crash-corrupted empty-schema table 'X'`.
  - **Special-case `aja_missions`**: after recreation, lazily imports and calls `rebuild_all_mission_projections()` — JSONL journals under `DATA_DIR/missions` are source of truth, fully restoring history.
  - Guarantees: never drops non-empty-schema tables; never touches unknown tables (iteration is over known names only); per-table try/except — open failure, drop/recreate failure, or projection-rebuild failure all log a warning and startup continues.

No schema constants were changed; no other files modified.

### 2. New tests — `tests/python/unit/test_nightshift_wave3_repair.py` (5 tests)

| Test | Pins |
|---|---|
| `test_empty_schema_missions_table_is_repaired` | corrupt `aja_missions` via empty schema → init → schema equals `MISSIONS_SCHEMA`, filtered queries work, warning logged verbatim |
| `test_repair_rebuilds_projections_from_journal` | fake `mission_wave3test.jsonl` (CREATED + STATUS_CHANGED) under tmp `DATA_DIR/missions` → after repair `get_mission()` returns goal/status/priority restored from journal |
| `test_nonempty_and_unknown_tables_never_touched` | `aja_tasks` with non-canonical-but-non-empty schema survives untouched; unknown empty-schema `aja_unknown` left alone |
| `test_multiple_known_tables_all_repaired` | workers + missions + chat_history corrupted together → all three match canonical schemas |
| `test_single_table_failure_does_not_block_startup` | injected `drop_table` failure for one table → AJAMemory still constructs, other corruption still repaired |

Hermeticity follows the established pattern (`tests/python/benchmarks/test_perf_baselines.py`): monkeypatch `sec_module.DATA_DIR`, `aja.runtime.mission_journal.DATA_DIR`, and reset `sec_module._instance = None` (the rebuild path resolves its write target through the `get_aja_memory()` singleton).

## Test Results

```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave3_repair.py -q --timeout=300 -p no:cacheprovider
→ 5 passed in 3.37s

py -3.12 -m pytest tests/python/unit/test_nightshift_wave3_repair.py tests/python/unit \
  -k "secretar or mission or repair or runtime" -q -n 2 --timeout=300 -p no:cacheprovider
→ 58 passed, 1 skipped in 16.12s
```
The single skip is pre-existing and unrelated (`test_nightshift_wave2_e2.py::… SSE httpx buffering`, documented at source).

## Observed LanceDB API Behavior (lancedb 0.30.2) — Empty-Schema Creation

Probed live on this machine:

1. **`db.create_table("name")` with NO schema/data raises** `ValueError: Either data or schema must be provided` — the API refuses to mint an empty-schema table directly. The live corruption therefore came from torn metadata during a crashed write, not from a bare `create_table` call.
2. **Reproducing the signature requires an explicit empty Arrow schema**: `db.create_table("name", schema=pa.schema([]))` succeeds and yields a table whose `.schema` has **0 fields**. Same for `data=pa.table({})`. This is what the tests use to simulate the crash state.
3. The corrupted table **appears in `db.list_tables()`** (so `_init_tables`' "not in existing" creation guard skips it silently — exactly why the incident persisted across restarts).
4. `open_table()` on it **succeeds**; `len(table.schema) == 0`; `count_rows()` returns 0 without error.
5. Any filtered query reproduces the incident exactly: `RuntimeError lance error: LanceError(Schema): Schema error: No field named status. Valid fields are _rowid, _rowaddr …`.
6. **Drop + recreate works cleanly on such tables**: `db.drop_table(name)` then `create_table(name, schema=…)` restores full function; rows add and query normally afterwards.

## Operational Note

Repair runs at every `AJAMemory` construction (any process startup touching memory). For the currently-corrupted machine DB, simply starting any AJA entrypoint will heal `aja_missions` and replay the JSONL journals back into it.
