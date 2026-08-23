"""Baton pickup latency benchmarks: Columnar v2 vs legacy JSON v1.

Deselected by default (``benchmark`` marker). Run with::

    py -3.12 -m pytest tests/python/benchmarks/test_baton_scales.py -m benchmark -s

Measures capture and cold-cache pickup separately for synthetic histories of
N = 10 / 100 / 1000 / 10000 turns (~200-char content each). xdist-safe: unique
per-test baton subdirectories on top of conftest's per-worker AJA_DATA_DIR.
"""

import time
import uuid

import pytest

from aja.runtime.baton_state import BatonCorruptionError, read_baton
from aja.runtime.handover import BatonManager

pytestmark = [pytest.mark.benchmark]

SIZES = [10, 100, 1000, 10000]
_CONTENT = "x" * 180 + " turn payload {i} é中文"


def _make_history(n: int):
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": _CONTENT.format(i=i),
            **({"timestamp": 1700000000.0 + i} if i % 3 == 0 else {}),
        }
        for i in range(n)
    ]


def _fresh_manager(tmp_path) -> BatonManager:
    manager = BatonManager()
    manager.baton_dir = tmp_path / f"batons-{uuid.uuid4().hex}"
    manager.baton_dir.mkdir(parents=True, exist_ok=True)
    return manager


def test_baton_v1_vs_v2_scale_table(tmp_path, monkeypatch):
    """Prints the v1-vs-v2 capture/pickup table; asserts sanity ceilings only."""
    results = {}

    # Warmup (imports, pyarrow codecs, OS file cache)
    warm = _fresh_manager(tmp_path)
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    wc = warm.capture("warmup", {"run_id": "w", "history": _make_history(10), "metadata": {}})
    warm.clear_memory_cache()
    warm.pickup(wc)

    for n in SIZES:
        history = _make_history(n)
        row = {}
        for label, env in (("v1", "1"), ("v2", None)):
            if env is None:
                monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
            else:
                monkeypatch.setenv("AJA_BATON_SCHEMA", env)
            manager = _fresh_manager(tmp_path)
            state = {"run_id": f"bench-{label}-{n}", "history": history, "metadata": {}}

            t0 = time.perf_counter()
            code = manager.capture(f"scale mission n={n}", state, trace_id=f"t-{label}-{n}")
            capture_ms = (time.perf_counter() - t0) * 1000.0

            manager.clear_memory_cache()
            t0 = time.perf_counter()
            picked = manager.pickup(code)
            pickup_ms = (time.perf_counter() - t0) * 1000.0

            assert picked is not None
            row[label] = {"capture_ms": capture_ms, "pickup_ms": pickup_ms}

        results[n] = row
        print(
            f"[perf] baton N={n:>6}: "
            f"capture v1={row['v1']['capture_ms']:9.2f}ms v2={row['v2']['capture_ms']:9.2f}ms | "
            f"pickup v1={row['v1']['pickup_ms']:9.2f}ms v2={row['v2']['pickup_ms']:9.2f}ms"
        )

    # Sanity ceiling: v2 cold pickup at 10k turns stays under 250ms.
    v2_10k_pickup = results[10000]["v2"]["pickup_ms"]
    assert v2_10k_pickup < 250.0, f"v2 pickup@10k took {v2_10k_pickup:.1f}ms (ceiling 250ms)"

    # No full-json-parse regression: v2 lazy pickup must not be slower than the
    # legacy full-json.loads pickup at 10k (generous 50% + 25ms scheduling slack).
    v1_10k_pickup = results[10000]["v1"]["pickup_ms"]
    assert v2_10k_pickup <= v1_10k_pickup * 1.5 + 25.0, (
        f"v2 pickup regression: v2={v2_10k_pickup:.1f}ms vs v1={v1_10k_pickup:.1f}ms"
    )

    # Corruption contract holds at scale too (truncated v2 -> typed error).
    manager = _fresh_manager(tmp_path)
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    code = manager.capture("corrupt scale", {"run_id": "cs", "history": _make_history(100), "metadata": {}})
    arrow_path = manager.baton_dir / f"baton_{code}.arrow"
    data = arrow_path.read_bytes()
    arrow_path.write_bytes(data[: len(data) // 2])
    with pytest.raises(BatonCorruptionError):
        read_baton(arrow_path)


def test_baton_lazy_turn_access_at_scale(tmp_path, monkeypatch):
    """Lazy per-turn access touches only columnar lists — no json.loads of history."""
    from aja.runtime.baton_state import ColumnarBatonState

    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    manager = _fresh_manager(tmp_path)
    code = manager.capture(
        "lazy access",
        {"run_id": "lazy-run", "history": _make_history(10000), "metadata": {}},
    )
    manager.clear_memory_cache()
    state = manager.pickup(code)
    assert isinstance(state, ColumnarBatonState)

    t0 = time.perf_counter()
    first = state.turn(0)
    last = state.turn(-1)
    slice_turns = list(state.iter_turns(0, 10))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert last["content"].endswith("turn payload 9999 é中文")
    assert first["role"] == "user"
    assert len(slice_turns) == 10
    print(f"[perf] baton lazy random/slice access @10k turns: {elapsed_ms:.3f}ms")
    assert elapsed_ms < 250.0
