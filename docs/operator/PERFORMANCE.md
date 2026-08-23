# AJA Performance Baselines

Measured on this development machine (Windows, Python 3.12.10) — 2026-08-23.
Numbers are wall-clock via `time.perf_counter`, one warmup pass excluded.

## Running the benchmarks

Benchmarks carry the `benchmark` marker and are **deselected by default**:

```bash
py -3.12 -m pytest tests/python/benchmarks -m benchmark          # only benchmarks
py -3.12 -m pytest tests/python -m "not benchmark"               # normal suite (unchanged)
py -3.12 -m pytest tests/python/benchmarks -m benchmark -s      # show [perf] numbers
```

> ⚠️ **pyproject.toml needs** `benchmark: Performance baseline measurements` added to
> `[tool.pytest.ini_options].markers`. Until then pytest emits a harmless
> `PytestUnknownMarkWarning`.

## Running the profiler

```bash
# Default: no-LLM micro-suite (registry + journal + embedding)
py -3.12 scripts/profile_mission.py [--top 25]

# Individual components (no network/LLM needed)
py -3.12 scripts/profile_mission.py --target registry   --top 10
py -3.12 scripts/profile_mission.py --target journal    --top 15
py -3.12 scripts/profile_mission.py --target embedding  --top 10

# SwarmEngine dry-run planning path (REQUIRES a configured LLM key)
py -3.12 scripts/profile_mission.py --target swarm --objective "Perform project analysis"
```

The script redirects `AJA_DATA_DIR` to a throwaway temp dir and forces
`AJA_MOCK_EMBEDDINGS=1`, so profiling never touches operator state or the network
(except explicit `--target swarm`).

## Baseline numbers (this machine)

| Operation | Mean | Median | p99 | Max | Rate |
|---|---|---|---|---|---|
| `classify_command()` per call (8-cmd mixed batch / 8) | 1.26 ms | — | ~1.56 ms | batch max 12.5 ms | ~790/s |
| `get_text_embedding()` mock hash vector | 0.005 ms | 0.005 ms | 0.006 ms | 0.010 ms | — |
| `get_text_embedding()` real MiniLM warm encode | 10.14 ms | 10.18 ms | 11.92 ms | 11.92 ms | ~99/s |
| `AJAMemory.create_task`+`get_task` LanceDB round-trip | 33.9 ms | 29.8 ms | 64.7 ms | 64.7 ms | ~29.5 ops/s |
| `MissionJournal.emit()` (append + projection rebuild, hermetic DB) | 37.9 ms | 38.4 ms | 40.3 ms | 40.3 ms | ~26.4 events/s |
| `NativeToolRegistry.execute("get_datetime")` dispatch | 0.009 ms | 0.009 ms | 0.017 ms | 0.019 ms | ~110k/s |
| `NativeToolRegistry.execute()` unknown-tool miss path | <0.001 ms | — | — | 0.001 ms | — |

Contract check: `classify_command` stays far under the 50 ms QA ceiling (max 12.5 ms for a
full 8-command compound batch; typical single commands classify in well under 2 ms).

## Notes & surprises found while measuring

1. **Cold-start trap in serial runs**: `AJAMemory._init_tables` triggers
   `rebuild_all_mission_projections()` when it creates the `aja_missions` table on a fresh
   DB. Without xdist's `AJA_DATA_DIR` isolation this replays the *operator's entire mission
   history* (took >100 s on this host). The benchmarks therefore redirect both
   `secretary.DATA_DIR` and `mission_journal.DATA_DIR` to `tmp_path`, making them hermetic.
   Under the standard parallel suite (`-n 8`) conftest isolation already prevents this.
2. **Journal emit cost is projection-dominated**: the JSONL append itself is microseconds;
   nearly all of the ~38 ms is the write-through LanceDB projection rebuild (`read_events`
   → reducer → table search/update). Cost also grows with event count since projections
   replay the full journal each emit.
3. **Real MiniLM warm latency (~10 ms)** means embedding-heavy territory scans are compute
   bound at roughly 100 chunks/s/core with the real model vs ~200k/s mocked.
4. Registry dispatch overhead is negligible (<10 µs); any tool-call latency lives in the
   tool implementations, not the dispatcher.

## Baton v1 vs v2 (Columnar Schema, 2026-08-23)

Capture and cold-cache pickup latency for synthetic mission histories (~200-char turns,
`AJA_BATON_SCHEMA=1` vs default v2). Run via
`py -3.12 -m pytest tests/python/benchmarks/test_baton_scales.py -m benchmark -s`.

| N turns | capture v1 | capture v2 | pickup v1 (full json.loads) | pickup v2 (lazy columnar) |
|---|---|---|---|---|
| 10 | 2.0 ms | 2.0 ms | 9.3 ms | 14.6 ms |
| 100 | 2.4 ms | 3.0 ms | 16.9 ms | 15.6 ms |
| 1000 | 3.5 ms | 6.7 ms | 17.1 ms | 15.9 ms |
| 10000 | 16.8 ms | 46.9 ms | 35.5 ms | **18.2 ms** |

Lazy random/slice turn access at 10k turns (`turn(0)`/`turn(-1)`/10-turn slice): ~96–112 ms.

Analysis: v2 pays ~2–3× capture cost building the columnar lists but breaks even on pickup
by N≈100 and wins ~2× at 10k turns (18 ms, well under the 250 ms ceiling), while `to_state()`
stays byte-identical to the legacy shape — a good trade for long missions; small histories are
noise-dominated (pickup floor ≈ 9–16 ms is mmap/IPC open cost).
