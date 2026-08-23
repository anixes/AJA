"""
Columnar Baton Schema v2 tests:
- lazy per-turn columnar history (no full JSON parse on pickup)
- legacy v1 read compatibility
- AJA_BATON_SCHEMA env gating
- corruption detection (BatonCorruptionError)
- large-history pickup latency ceiling
"""

import json
import time

import pyarrow as pa
import pytest

from aja.runtime.baton_state import (
    SCHEMA_VERSION,
    BatonCorruptionError,
    ColumnarBatonState,
    LegacyJSONState,
    baton_v2_schema,
    read_baton,
    write_baton_v2,
)
from aja.runtime.handover import BatonManager


HISTORY = [
    {"role": "user", "content": "Start the mission"},
    {"role": "assistant", "content": "Planning now", "timestamp": 1234.5},
    {"role": "user", "content": "Proceed"},
]


@pytest.fixture
def manager():
    return BatonManager()


def _capture_default(manager, monkeypatch):
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    return manager.capture(
        "Columnar mission objective",
        {"run_id": "v2-run-1", "history": HISTORY, "metadata": {"k": "v"}},
        trace_id="trace-v2-1",
    )


def test_v2_round_trip(manager, monkeypatch):
    code = _capture_default(manager, monkeypatch)

    # Cache fast path returns a ColumnarBatonState
    state = manager.pickup(code)
    assert isinstance(state, ColumnarBatonState)
    assert state.schema_version == SCHEMA_VERSION == 2
    assert len(state) == len(HISTORY)
    for i, expected in enumerate(HISTORY):
        turn = state.turn(i)
        assert turn["role"] == expected.get("role", "")
        assert turn["content"] == expected["content"]
        assert isinstance(turn["ts"], float)

    # Cold disk path (mmap) after cache clear
    manager.clear_memory_cache()
    cold = manager.pickup(code)
    assert isinstance(cold, ColumnarBatonState)
    assert cold.to_list()[1]["content"] == "Planning now"
    assert [t["content"] for t in cold.iter_turns(0, 2)] == [
        "Start the mission",
        "Planning now",
    ]


def test_to_state_legacy_shape_equivalence(manager, monkeypatch):
    code = _capture_default(manager, monkeypatch)
    state = manager.pickup(code)

    # Dict-index compat surface used by consumers
    assert state["objective"] == "Columnar mission objective"
    assert state["run_id"] == "v2-run-1"
    assert state["metadata"]["trace_id"] == "trace-v2-1"
    assert state.metadata["trace_id"] == "trace-v2-1"

    legacy = state.to_state()
    assert set(legacy.keys()) == {"objective", "run_id", "history", "metadata"}
    assert legacy["history"] == HISTORY
    assert legacy["metadata"]["trace_id"] == "trace-v2-1"


def test_v1_file_still_readable(manager, tmp_path, monkeypatch):
    """A baton written with the old 4-column schema is pickup-compatible."""
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    code = "V1AA01"
    arrow_path = manager.baton_dir / f"baton_{code}.arrow"
    history = [{"role": "user", "content": "legacy hello"}]
    schema = pa.schema([
        ("objective", pa.string()),
        ("run_id", pa.string()),
        ("history_json", pa.string()),
        ("metadata_json", pa.string()),
    ])
    batch = pa.RecordBatch.from_arrays([
        pa.array(["Legacy objective"], type=pa.string()),
        pa.array(["v1-run"], type=pa.string()),
        pa.array([json.dumps(history)], type=pa.string()),
        pa.array([json.dumps({"trace_id": "trace-v1"})], type=pa.string()),
    ], schema=schema)
    with pa.OSFile(str(arrow_path), "wb") as sink:
        with pa.ipc.new_file(sink, schema) as writer:
            writer.write_batch(batch)
    meta_path = manager.baton_dir / f"baton_{code}.json"
    meta_path.write_text(json.dumps({"code": code, "arrow_ref": str(arrow_path)}))

    state = manager.pickup(code)
    assert isinstance(state, LegacyJSONState)
    assert state["objective"] == "Legacy objective"
    assert state["history"] == history
    assert state["metadata"]["trace_id"] == "trace-v1"
    assert len(state) == 1
    assert state.turn(0)["content"] == "legacy hello"


def test_env_schema_1_produces_v1_file(manager, monkeypatch):
    monkeypatch.setenv("AJA_BATON_SCHEMA", "1")
    code = manager.capture(
        "v1 forced",
        {"run_id": "force-v1", "history": [{"role": "user", "content": "x"}], "metadata": {}},
        trace_id="trace-f1",
    )
    with pa.memory_map(str(manager.baton_dir / f"baton_{code}.arrow"), mode="r") as src:
        names = pa.ipc.open_file(src).read_all().schema.names
    assert "schema_version" not in names
    assert "hist_role" not in names
    assert "history_json" in names


def test_truncated_arrow_raises_corruption(manager, monkeypatch):
    code = _capture_default(manager, monkeypatch)
    manager.clear_memory_cache()
    arrow_path = manager.baton_dir / f"baton_{code}.arrow"
    data = arrow_path.read_bytes()
    arrow_path.write_bytes(data[: len(data) // 2])

    with pytest.raises(BatonCorruptionError):
        manager.pickup(code)
    with pytest.raises(BatonCorruptionError):
        read_baton(arrow_path)


def test_large_history_pickup_latency(manager, monkeypatch):
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    big_history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} payload"}
        for i in range(5000)
    ]
    code = manager.capture(
        "large history mission",
        {"run_id": "big-run", "history": big_history, "metadata": {}},
        trace_id="trace-big",
    )
    manager.clear_memory_cache()

    start = time.perf_counter()
    state = manager.pickup(code)
    elapsed = time.perf_counter() - start

    assert isinstance(state, ColumnarBatonState)
    assert len(state) == 5000
    assert state.turn(4999)["content"] == "turn 4999 payload"
    # Budget is <50ms; generous CI ceiling at 5x to avoid flakiness.
    assert elapsed < 0.25, f"pickup took {elapsed*1000:.1f}ms (budget 250ms)"


def test_write_baton_v2_direct(tmp_path):
    path = tmp_path / "direct.arrow"
    write_baton_v2(path, {
        "objective": "direct",
        "run_id": "d1",
        "history": [{"role": "user", "content": "hey"}],
        "metadata": {"trace_id": "t9"},
    })
    state = read_baton(path)
    assert isinstance(state, ColumnarBatonState)
    assert state.trace_id == "t9"
    assert tuple(baton_v2_schema().names)[:4] == (
        "objective",
        "run_id",
        "history_json",
        "metadata_json",
    )
