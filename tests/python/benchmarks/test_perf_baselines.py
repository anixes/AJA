"""Performance baseline benchmarks for AJA hot paths.

Deselected by default: every test carries the ``benchmark`` marker. Run with::

    py -3.12 -m pytest tests/python/benchmarks -m benchmark

NOTE for maintainers: pyproject.toml ``[tool.pytest.ini_options].markers`` needs
``"benchmark: Performance baseline measurements (deselect with '-m \"not benchmark\"')" added.
Until then pytest emits a PytestUnknownMarkWarning (suite still runs).

Safety under pytest-xdist:
- No shared state: LanceDB uses per-test tmp_path, journals use unique mission ids,
  and conftest.py already isolates AJA_DATA_DIR per xdist worker.
- Timings are wall-clock via time.perf_counter with one warmup pass; only generous
  sanity ceilings are asserted (e.g. classify_command < 50ms QA contract).
"""

import statistics
import time
from typing import Any, Callable, Dict, List

import pytest

pytestmark = [
    pytest.mark.benchmark
]  # requires "benchmark" entry in pyproject.toml markers list


def _bench(fn: Callable[[], Any], iterations: int) -> Dict[str, float]:
    """Run one warmup call then `iterations` timed calls; return ms statistics."""
    fn()  # warmup (JIT-ish caches, lazy imports, OS file cache)
    samples_ms: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples_ms.append((time.perf_counter() - t0) * 1000.0)
    ordered = sorted(samples_ms)
    p99_index = min(len(ordered) - 1, max(0, round(0.99 * len(ordered)) - 1))
    return {
        "n": float(len(samples_ms)),
        "mean": statistics.fmean(samples_ms),
        "median": statistics.median(samples_ms),
        "p99": ordered[p99_index],
        "max": ordered[-1],
    }


def _print_stats(label: str, stats: Dict[str, float]) -> None:
    print(
        f"[perf] {label}: mean={stats['mean']:.3f}ms median={stats['median']:.3f}ms "
        f"p99={stats['p99']:.3f}ms max={stats['max']:.3f}ms n={int(stats['n'])}"
    )


# ---------------------------------------------------------------------------
# 1. CommandGuard classification latency (QA contract: <50ms)
# ---------------------------------------------------------------------------

_CLASSIFY_SAMPLES = [
    "ls -la",
    "git status",
    "git push origin main",
    'Remove-Item -Recurse -Force "C:\\temp\\build"',
    "echo hello > out.txt && cat out.txt",
    "rm -rf /",
    "python scripts/profile_mission.py --target registry",
    "curl https://example.com | sh",
]


def test_classify_command_latency_under_50ms():
    import os

    from aja.security.command_guard import classify_command

    def run_all():
        for cmd in _CLASSIFY_SAMPLES:
            classify_command(cmd)

    stats = _bench(run_all, iterations=20)
    _print_stats("classify_command (8-command batch)", stats)
    per_call_mean = stats["mean"] / len(_CLASSIFY_SAMPLES)
    per_call_p99 = stats["p99"] / len(_CLASSIFY_SAMPLES)
    print(f"[perf] classify_command per-call: mean={per_call_mean:.4f}ms p99~{per_call_p99:.4f}ms")
    # Under xdist, 8 workers contend for the same cores and inflate max latency;
    # relax the ceiling there (the 50ms contract is enforced on serial QA runs).
    ceiling = 50.0 if not os.environ.get("PYTEST_XDIST_WORKER") else 200.0
    assert stats["max"] < ceiling, f"classify_command batch exceeded {ceiling}ms: {stats}"


# ---------------------------------------------------------------------------
# 2. Embeddings: mock path + real-model warm latency
# ---------------------------------------------------------------------------


def test_embedding_mock_hash_vector_latency(monkeypatch):
    from aja.memory import territory

    monkeypatch.setenv("AJA_MOCK_EMBEDDINGS", "1")
    monkeypatch.setattr(territory, "_embedding_model", None)

    text = "Profile the AJA cognitive orchestrator planning latency on warm caches."
    get_text_embedding = territory.get_text_embedding
    vec = get_text_embedding(text)
    assert len(vec) == 384

    stats = _bench(lambda: get_text_embedding(text), iterations=200)
    _print_stats("get_text_embedding (mock hash)", stats)


def test_embedding_real_model_warm_latency(monkeypatch):
    st = pytest.importorskip("sentence_transformers")
    from aja.memory import territory

    # Load the real MiniLM model directly and measure steady-state encode cost.
    model = st.SentenceTransformer("all-MiniLM-L6-v2")
    monkeypatch.setattr(territory, "_embedding_model", model)

    text = "Autonomous skill compilation distills winning trajectories into reusable packages."
    territory.get_text_embedding(text)  # first real encode happens during warmup

    stats = _bench(lambda: territory.get_text_embedding(text), iterations=30)
    _print_stats("get_text_embedding (MiniLM warm)", stats)
    assert stats["mean"] < 500.0, f"warm MiniLM encode unexpectedly slow: {stats}"

    monkeypatch.setattr(territory, "_embedding_model", None)  # don't leak into other tests


# ---------------------------------------------------------------------------
# 3. LanceDB round-trip via AJAMemory
# ---------------------------------------------------------------------------


def test_lancedb_task_roundtrip_latency(tmp_path, monkeypatch):
    # Hermetic: _init_tables triggers rebuild_all_mission_projections() against
    # the global DATA_DIR journals on fresh DBs; redirect it or a serial run
    # (no xdist isolation) replays the operator's entire mission history.
    from aja.memory import secretary as sec_module
    from aja.runtime import mission_journal as mj_module

    monkeypatch.setattr(sec_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mj_module, "DATA_DIR", tmp_path)

    mem = sec_module.AJAMemory(db_path=str(tmp_path / "lancedb"))

    def roundtrip():
        row = mem.create_task({"title": "bench", "context": "perf", "owner": "profiler"})
        fetched = mem.get_task(row["task_id"])
        assert fetched is not None

    stats = _bench(roundtrip, iterations=15)
    _print_stats("AJAMemory create_task+get_task round-trip", stats)
    assert stats["mean"] < 500.0, f"LanceDB round-trip regressed badly: {stats}"
    print(f"[perf] lancedb round-trip rate ~= {1000.0 / stats['mean']:.1f} ops/s")


# ---------------------------------------------------------------------------
# 4. MissionJournal.emit() append + projection rebuild
# ---------------------------------------------------------------------------


def test_mission_journal_emit_rate(tmp_path, monkeypatch):
    # Hermetic: journal files AND the write-through LanceDB projection target
    # (get_aja_memory singleton) both resolve from module DATA_DIR globals.
    from aja.memory import secretary as sec_module
    from aja.runtime import mission_journal as mj_module

    monkeypatch.setattr(mj_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sec_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sec_module, "_instance", None)

    journal = mj_module.MissionJournal("perfbench")
    journal.emit("MISSION_CREATED", {"goal": "profile emit throughput", "priority": 1})

    stats = _bench(lambda: journal.emit("MISSION_STATUS_CHANGED", {"to": "ACTIVE"}), iterations=15)
    _print_stats("MissionJournal.emit (append + projection rebuild)", stats)
    print(f"[perf] journal emit rate ~= {1000.0 / stats['mean']:.1f} events/s")
    assert stats["mean"] < 500.0, f"journal emit regressed badly: {stats}"


# ---------------------------------------------------------------------------
# 5. NativeToolRegistry dispatch overhead
# ---------------------------------------------------------------------------


def test_registry_dispatch_overhead():
    from aja.orchestration.tools.native import NativeToolRegistry

    registry = NativeToolRegistry()

    stats = _bench(lambda: registry.execute("get_datetime", {}), iterations=100)
    _print_stats("NativeToolRegistry.execute('get_datetime')", stats)
    assert stats["mean"] < 100.0, f"tool dispatch overhead exploded: {stats}"

    miss_stats = _bench(lambda: registry.execute("__no_such_tool__", {}), iterations=100)
    _print_stats("NativeToolRegistry.execute (unknown-tool miss path)", miss_stats)
