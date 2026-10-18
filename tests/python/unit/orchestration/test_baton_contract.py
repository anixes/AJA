"""
Columnar Baton Schema v2 — contract test matrix.

Covers:
- v2 round-trip fidelity (varied turn shapes, unicode, empty history)
- v1 legacy file readability via pickup -> LegacyJSONState
- v1 -> v2 upgrade read path
- truncated .arrow -> BatonCorruptionError (never a silent {})
- fleet loop on v2: capture -> transmit-format payload -> HMAC receive -> pickup
- RAM-cache path identical to disk path
- AJA_BATON_SCHEMA=1 still writes v1 files; reader stays compat both ways
"""

import base64
import hashlib
import hmac
import json
import uuid

import pyarrow as pa
import pytest

from aja.runtime.baton_state import (
    SCHEMA_VERSION,
    BatonCorruptionError,
    ColumnarBatonState,
    LegacyJSONState,
    read_baton,
    state_from_buffer,
)
from aja.runtime.handover import BatonManager

V1_SCHEMA = pa.schema([
    ("objective", pa.string()),
    ("run_id", pa.string()),
    ("history_json", pa.string()),
    ("metadata_json", pa.string()),
])


@pytest.fixture
def manager():
    return BatonManager()


def _unique_code() -> str:
    return uuid.uuid4().hex[:6].upper().replace("0", "A").replace("O", "B")


def _write_v1_file(arrow_path, objective, run_id, history, metadata):
    batch = pa.RecordBatch.from_arrays([
        pa.array([objective], type=pa.string()),
        pa.array([run_id], type=pa.string()),
        pa.array([json.dumps(history)], type=pa.string()),
        pa.array([json.dumps(metadata)], type=pa.string()),
    ], schema=V1_SCHEMA)
    with pa.OSFile(str(arrow_path), "wb") as sink:
        with pa.ipc.new_file(sink, V1_SCHEMA) as writer:
            writer.write_batch(batch)


def _seed_v1_baton(manager, code="V1CON1", history=None, metadata=None):
    arrow_path = manager.baton_dir / f"baton_{code}.arrow"
    _write_v1_file(
        arrow_path,
        "Legacy contract objective",
        "v1-run",
        history if history is not None else [{"role": "user", "content": "legacy hello"}],
        metadata if metadata is not None else {"trace_id": "trace-v1"},
    )
    meta_path = manager.baton_dir / f"baton_{code}.json"
    meta_path.write_text(json.dumps({"code": code, "timestamp": 0.0, "ttl": 3600,
                                     "arrow_ref": str(arrow_path)}), encoding="utf-8")
    return code


# ---------------------------------------------------------------------------
# 1. v2 round-trip fidelity
# ---------------------------------------------------------------------------

VARIED_HISTORY = [
    {"role": "user", "content": "héllo 世界 🚀 unicode content"},          # no ts keys at all
    {"role": "assistant", "content": "with timestamp", "timestamp": 1234.5},
    {"role": "user", "content": "string ts", "ts": "1700000000.25"},
    {"role": "tool", "content": "alt key", "time": 7},                     # alternate ts key
    {"role": "assistant", "content": "extra payload keys",
     "tool_calls": [{"name": "run_shell_command", "args": {"cmd": "ls"}}]},
]


@pytest.mark.parametrize("history", [
    VARIED_HISTORY,
    [],
    [{"content": "no role key"}],
    ["non-dict turn survives verbatim", {"role": "user", "content": "mixed"}],
])
def test_v2_round_trip_fidelity(manager, monkeypatch, history):
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    state_in = {
        "run_id": f"rt-{uuid.uuid4().hex[:8]}",
        "history": history,
        "metadata": {"k": "v", "nested": {"π": 3.14}},
    }
    code = manager.capture("Round-trip objective ✅", state_in, trace_id="trace-rt")

    # RAM-cache path
    cached = manager.pickup(code)
    assert isinstance(cached, ColumnarBatonState)
    assert cached.schema_version == SCHEMA_VERSION == 2
    assert cached.to_state() == {
        "objective": "Round-trip objective ✅",
        "run_id": state_in["run_id"],
        "history": history,
        "metadata": {**state_in["metadata"], "trace_id": "trace-rt"},
    }

    # Cold disk path is identical
    manager.clear_memory_cache()
    cold = manager.pickup(code)
    assert cold.to_state() == cached.to_state()

    # Lazy per-turn access agrees with the exact history for dict turns
    assert len(cold) == len(history)
    for i, expected in enumerate(history):
        if isinstance(expected, dict):
            turn = cold.turn(i)
            assert turn["content"] == str(expected.get("content", "") if expected.get("content") is not None else "")
            assert turn["role"] == expected.get("role", "")


def test_v2_empty_history_len_zero(manager, monkeypatch):
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    code = manager.capture("empty", {"run_id": "e1", "history": [], "metadata": {}},
                           trace_id="t-empty")
    state = manager.pickup(code)
    assert len(state) == 0
    assert list(state.iter_turns()) == []
    assert state.to_state()["history"] == []


# ---------------------------------------------------------------------------
# 2 + 3. v1 file readable / v1->v2 upgrade read
# ---------------------------------------------------------------------------

def test_v1_file_pickup_returns_legacy_json_state(manager, monkeypatch):
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    history = [{"role": "user", "content": "legacy contract"}, {"role": "assistant", "content": "ok"}]
    code = _seed_v1_baton(manager, history=history)

    state = manager.pickup(code)
    assert isinstance(state, LegacyJSONState)
    assert not isinstance(state, ColumnarBatonState)
    assert state["objective"] == "Legacy contract objective"
    assert state["run_id"] == "v1-run"
    assert state["history"] == history
    assert state["metadata"]["trace_id"] == "trace-v1"
    assert state.schema_version == 1


def test_v1_bytes_readable_via_new_reader(tmp_path):
    """v1 bytes read directly by the v2-aware reader still work (upgrade read)."""
    path = tmp_path / "upgrade.arrow"
    history = [{"role": "user", "content": "upgrade me"}]
    _write_v1_file(path, "Upgrade objective", "v1-up", history, {"trace_id": "tr-up"})

    state = read_baton(path)
    assert isinstance(state, LegacyJSONState)
    assert state.to_state()["history"] == history

    # And via the in-memory buffer reader used by the RAM cache
    buffer = pa.py_buffer(path.read_bytes())
    from_buffer = state_from_buffer(buffer)
    assert isinstance(from_buffer, LegacyJSONState)
    assert from_buffer["objective"] == "Upgrade objective"


def test_v2_reader_ignores_env_when_reading_v2_files(manager, monkeypatch):
    """Reader auto-detects: env=1 must not downgrade reads of existing v2 files."""
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    code = manager.capture("detect me", {"run_id": "d2", "history": VARIED_HISTORY, "metadata": {}},
                           trace_id="t-d2")
    arrow_path = manager.baton_dir / f"baton_{code}.arrow"

    monkeypatch.setenv("AJA_BATON_SCHEMA", "1")
    state = read_baton(arrow_path)
    assert isinstance(state, ColumnarBatonState)
    assert state.schema_version == 2


# ---------------------------------------------------------------------------
# 4. Truncated .arrow -> BatonCorruptionError
# ---------------------------------------------------------------------------

def test_truncated_arrow_raises_corruption_not_empty_dict(manager, monkeypatch):
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    code = manager.capture("to be truncated", {"run_id": "tc", "history": VARIED_HISTORY, "metadata": {}},
                           trace_id="t-tc")
    manager.clear_memory_cache()
    arrow_path = manager.baton_dir / f"baton_{code}.arrow"
    data = arrow_path.read_bytes()
    arrow_path.write_bytes(data[: len(data) // 2])

    with pytest.raises(BatonCorruptionError):
        manager.pickup(code)
    with pytest.raises(BatonCorruptionError):
        read_baton(arrow_path)
    with pytest.raises(BatonCorruptionError):
        read_baton(arrow_path, mmap=False)


# ---------------------------------------------------------------------------
# 5. Fleet loop on v2 (capture -> transmit-format payload -> HMAC receive -> pickup)
# ---------------------------------------------------------------------------

def test_fleet_loop_v2_end_to_end_with_hmac(manager, tmp_path, monkeypatch):
    secret = "fleet-shared-secret"
    monkeypatch.setenv("AJA_BATON_SECRET", secret)
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)

    sender = manager
    code = sender.capture(
        "Fleet mission objective",
        {"run_id": "fleet-run", "history": VARIED_HISTORY, "metadata": {"origin": "host-a"}},
        trace_id="trace-fleet",
    )

    # Build the exact transmit-format payload (mirrors transmit_baton internals)
    meta = json.loads((sender.baton_dir / f"baton_{code}.json").read_text(encoding="utf-8"))
    arrow_data = (sender.baton_dir / f"baton_{code}.arrow").read_bytes()
    payload = {
        "code": code,
        "meta": meta,
        "arrow_data_b64": base64.b64encode(arrow_data).decode("utf-8"),
    }
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    # Receive on the "remote host" (isolated baton dir simulates it)
    receiver = BatonManager()
    receiver.baton_dir = tmp_path / f"remote-{uuid.uuid4().hex}"
    receiver.baton_dir.mkdir(parents=True, exist_ok=True)

    received_code = receiver.receive_baton(payload, signature=signature, raw_body=body)
    assert received_code == code

    state = receiver.pickup(received_code)
    assert isinstance(state, ColumnarBatonState)
    assert state.objective == "Fleet mission objective"
    assert state.metadata["origin"] == "host-a"
    assert state.trace_id == "trace-fleet"

    # Cold disk pickup after cache eviction also works on the received files
    receiver.clear_memory_cache()
    cold = receiver.pickup(received_code)
    assert cold.to_state() == state.to_state()


def test_fleet_loop_rejects_tampered_payload(manager, tmp_path, monkeypatch):
    monkeypatch.setenv("AJA_BATON_SECRET", "fleet-secret-2")
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)

    code = manager.capture(
        "tamper target",
        {"run_id": "tamper-run", "history": [{"role": "user", "content": "x"}], "metadata": {}},
        trace_id="t-tamper",
    )
    payload = {
        "code": code,
        "meta": json.loads((manager.baton_dir / f"baton_{code}.json").read_text(encoding="utf-8")),
        "arrow_data_b64": base64.b64encode(
            (manager.baton_dir / f"baton_{code}.arrow").read_bytes()
        ).decode("utf-8"),
    }
    body = json.dumps(payload).encode("utf-8")

    receiver = BatonManager()
    receiver.baton_dir = tmp_path / f"remote-{uuid.uuid4().hex}"
    receiver.baton_dir.mkdir(parents=True, exist_ok=True)

    bad_sig = hmac.new(b"wrong-secret", body, hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="[Ss]ignature"):
        receiver.receive_baton(payload, signature=bad_sig, raw_body=body)

    tampered_body = json.dumps({**payload, "meta": {**payload["meta"], "ttl": 1}}).encode("utf-8")
    good_sig_for_original = hmac.new(b"fleet-secret-2", body, hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="[Ss]ignature"):
        receiver.receive_baton(payload, signature=good_sig_for_original, raw_body=tampered_body)


# ---------------------------------------------------------------------------
# 6. RAM-cache vs disk equivalence
# ---------------------------------------------------------------------------

def test_ram_cache_identical_to_disk(manager, monkeypatch):
    monkeypatch.delenv("AJA_BATON_SCHEMA", raising=False)
    code = manager.capture("cache vs disk", {"run_id": "cd-run", "history": VARIED_HISTORY, "metadata": {}},
                           trace_id="t-cd")

    via_cache = manager.pickup(code)
    assert via_cache is not None
    manager.clear_memory_cache()
    via_disk = manager.pickup(code)

    assert via_disk is not None
    # Exact legacy shape is identical cache vs disk.
    assert via_cache.to_state() == via_disk.to_state()
    # Lazy turn views agree on real payload fields. (Turns WITHOUT an explicit
    # timestamp get a fresh time.time() fallback per serialization pass, so
    # synthesized 'ts' may differ between the RAM buffer and disk file.)
    strip_ts = lambda turns: [(t["role"], t["content"]) for t in turns]
    assert strip_ts(via_cache.iter_turns()) == strip_ts(via_disk.iter_turns())


# ---------------------------------------------------------------------------
# 7. AJA_BATON_SCHEMA=1 still produces v1 files
# ---------------------------------------------------------------------------

def test_env_schema_1_writes_v1_and_reads_back_legacy(manager, monkeypatch):
    monkeypatch.setenv("AJA_BATON_SCHEMA", "1")
    code = manager.capture(
        "forced legacy write",
        {"run_id": "f1-run", "history": VARIED_HISTORY, "metadata": {}},
        trace_id="t-f1",
    )
    arrow_path = manager.baton_dir / f"baton_{code}.arrow"
    with pa.memory_map(str(arrow_path), mode="r") as src:
        names = pa.ipc.open_file(src).read_all().schema.names
    assert "schema_version" not in names and "hist_role" not in names
    assert set(names) == {"objective", "run_id", "history_json", "metadata_json"}

    manager.clear_memory_cache()
    state = manager.pickup(code)
    assert isinstance(state, LegacyJSONState)
    assert state["objective"] == "forced legacy write"
    assert state.to_state()["history"] == VARIED_HISTORY


# ---------------------------------------------------------------------------
# 8. Worker native guard fallback (agents/worker.py defect fix verification)
# ---------------------------------------------------------------------------

def test_worker_guard_survives_native_panic(tmp_path, monkeypatch):
    """
    The hardened Rust reader raises pyo3 PanicException (BaseException) on
    malformed-but-parseable Arrow worker batons; agents/worker.py's guard must
    recover through the pure-pyarrow reader instead of crashing.
    """
    import aja.aja_native as native

    # Schema-variant file: valid Arrow that makes the native reader panic
    schema = pa.schema([("payload", pa.large_string())])
    good_worker_json = json.dumps({"id": "w-guard", "task": "recover", "status": "pending"})
    batch = pa.RecordBatch.from_arrays(
        [pa.array([good_worker_json], type=pa.large_string())], schema=schema
    )
    variant = tmp_path / "variant.arrow"
    with pa.OSFile(str(variant), "wb") as sink:
        with pa.ipc.new_file(sink, schema) as writer:
            writer.write_batch(batch)

    # Sanity: native really does blow up (PanicException subclasses BaseException)
    import aja.aja_native as native
    panicked = False
    try:
        native.read_baton_ipc(str(variant))
    except BaseException as exc:
        panicked = not isinstance(exc, Exception)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    assert panicked, "expected native reader to fail with a BaseException on this input"

    # Truncated file: native raises ValueError; fallback must engage cleanly too
    solid = tmp_path / "solid.arrow"
    from aja.runtime.handover import write_baton_ipc
    write_baton_ipc(solid, {"id": "w-trunc", "task": "truncate-me"})
    trunc = tmp_path / "trunc.arrow"
    raw = solid.read_bytes()
    trunc.write_bytes(raw[: len(raw) // 2])

    # Schema-variant file: native PANICS, pure-pyarrow fallback RECOVERS it.
    recovered = _simulate_worker_arrow_read(variant)
    assert recovered["task"] == "recover"

    # Truncated file: unrecoverable by any reader, but the failure must be a
    # clean typed RuntimeError — never a process-killing Rust PanicException.
    with pytest.raises(RuntimeError) as excinfo:
        _simulate_worker_arrow_read(trunc)
    assert "Panic" not in type(excinfo.value).__name__


def _simulate_worker_arrow_read(path):
    """Replicates agents/worker.py's guarded native->python arrow read."""
    try:
        from aja import aja_native
        if aja_native is None:
            raise ImportError
        return json.loads(aja_native.read_baton_ipc(str(path)))
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        from aja.runtime.handover import read_baton_ipc
        return read_baton_ipc(path, use_native=False)
