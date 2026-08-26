# Memory Leak / Hang Findings — aja_runtime_events

Date: 2026-08-26 (overnight session)

## TL;DR

The production LanceDB table `aja_runtime_events` has **16,977 data fragments**
and **5,641 versions** (7 GB on disk, 16,855 rows). Every `table.add()` in
LanceDB creates a new fragment + version commit. Two consequences:

1. **The hang**: opening the table and each add gets progressively slower as
   fragment count grows; at ~17k fragments a single `open_table`/metadata scan
   takes so long that tests calling `tracker.log_event()` per plan-node
   (`execution_bridge._emit_debug_trace`) stall past pytest timeouts. The
   faulthandler stack confirms: main thread blocked in
   `lancedb/table.py add -> background_loop.run -> future.result()`, waiting
   on the LanceDB background IOCP loop.
2. **The RAM growth**: each open_table/add cycle maps fragment metadata;
   thousands of tiny fragments × mmap metadata across a long test session
   ratchets virtual memory into the multi-GB range. Combined with unclosed
   gateway caches (#1 in leak report), this produced the 18-22GB python.exe.

## Why production data is polluted

- `persistence/tracker.py log_event()` writes EVERY plan-node state trace to
  the same table used for real runtime events — including from unit tests
  (`test_parallel_serializability.py` runs hundreds of nodes; each emits
  PLAN_NODE_STATE_TRACE rows to the REAL DATA_DIR database because conftest
  only isolates AJA_DATA_DIR under xdist workers, NOT serial runs).
- Serial runs (our new default!) write directly to the production DB.
- No compaction/retention has ever run: 5,641 versions since inception.

## Fixes (in priority order)

1. IMMEDIATE: compact the table:
   `t.compact_segments(target_rows_per_fragment=100_000)` then
   `t.cleanup_old_versions()` — reclaims ~7GB → likely <100MB and restores
   add/open latency.
2. TEST ISOLATION GAP: conftest must set AJA_DATA_DIR to a temp dir for ALL
   runs (not just xdist workers). Serial runs currently pollute prod data.
3. RETENTION: periodic cleanup job for aja_runtime_events (e.g., keep 7 days,
   compact weekly) — belongs in scheduler or serve startup.
4. tracker.log_event should no-op (or write to a throwaway dir) when
   AJA_TEST_MODE/pytest is detected — plan-node traces don't belong in prod.

## Evidence

- faulthandler dump: main thread stuck in `background_loop.py:33 run ->
  future.result()` after `tracker.py:34 table.add(row)` called from
  `verification.py run_sequential` via `_emit_debug_trace`.
- Standalone repro: repeated `t.add()` degrades/hangs around the same point;
  fresh connect works fine until metadata reload.
- Disk: `aja_runtime_events.lance/data` = 16,977 files; `_versions` = 5,641.
- The stress test itself is FINE — it just does what it always did; the table
  grew past a threshold between Aug 25 and 26 (matches "worked yesterday").
