"""
Columnar Baton Schema v2 — lazy columnar mission-state batons.

v1 legacy layout: single-row IPC file with objective/run_id/history_json/
metadata_json utf8 columns; pickup required a full json.loads of history.

v2 layout: single-row IPC file that KEEPS columns 0..3 identical to v1
(objective, run_id, history_json, metadata_json) for on-disk compatibility,
then appends:
    trace_id        utf8
    schema_version  int32           (= SCHEMA_VERSION)
    created_at      timestamp[us]
    hist_role       list<utf8>       one element per turn
    hist_content    list<large_utf8> one element per turn
    hist_ts         list<float64>    one element per turn

Lazy reads (len/turn/iter_turns/to_list) touch only the list columns and
never json.loads the full history string. to_state() intentionally decodes
history_json so it reproduces the EXACT legacy pickup dict shape
(lossless round-trip of arbitrary per-turn keys).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

import pyarrow as pa

SCHEMA_VERSION = 2


class BatonCorruptionError(ValueError):
    """Raised when a baton file is truncated, unreadable, or malformed."""


def baton_v2_schema() -> pa.Schema:
    """Build the Columnar Baton v2 Arrow schema."""
    return pa.schema(
        [
            # --- legacy-compatible columns 0..3 (identical to v1) ---
            ("objective", pa.string()),
            ("run_id", pa.string()),
            ("history_json", pa.string()),
            ("metadata_json", pa.string()),
            # --- v2 additions ---
            ("trace_id", pa.string()),
            ("schema_version", pa.int32()),
            ("created_at", pa.timestamp("us")),
            ("hist_role", pa.list_(pa.string())),
            ("hist_content", pa.list_(pa.large_string())),
            ("hist_ts", pa.list_(pa.float64())),
        ]
    )


def _turn_timestamp(turn: Dict[str, Any], fallback: Optional[float] = None) -> float:
    """Best-effort per-turn timestamp extraction from common key spellings."""
    base = fallback if fallback is not None else time.time()
    for key in ("timestamp", "time", "ts"):
        raw = turn.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            return float(raw)
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return float(base)


def build_baton_table(state: Dict[str, Any]) -> pa.Table:
    """
    Build a single-row v2 Table from a mission state dict.

    Expected state shape: {"objective": str, "run_id": str,
    "history": [ {...}, ... ], "metadata": {...}} with trace_id already
    injected into metadata by the caller.
    """
    history = list(state.get("history", []))
    metadata = dict(state.get("metadata", {}))
    roles: List[str] = []
    contents: List[str] = []
    tss: List[float] = []
    now = time.time()
    for idx, turn in enumerate(history):
        if not isinstance(turn, dict):
            turn = {"content": str(turn)}
        roles.append(str(turn.get("role", "")))
        content = turn.get("content", "")
        contents.append("" if content is None else str(content))
        tss.append(_turn_timestamp(turn, fallback=now + idx * 1e-6))

    batch = pa.RecordBatch.from_arrays(
        [
            pa.array([str(state.get("objective", ""))], type=pa.string()),
            pa.array([str(state.get("run_id", "unknown"))], type=pa.string()),
            pa.array([json.dumps(history)], type=pa.string()),
            pa.array([json.dumps(metadata)], type=pa.string()),
            pa.array([str(metadata.get("trace_id", ""))], type=pa.string()),
            pa.array([SCHEMA_VERSION], type=pa.int32()),
            pa.array(
                [datetime.now(timezone.utc)], type=pa.timestamp("us")
            ),
            pa.array([roles], type=pa.list_(pa.string())),
            pa.array([contents], type=pa.list_(pa.large_string())),
            pa.array([tss], type=pa.list_(pa.float64())),
        ],
        schema=baton_v2_schema(),
    )
    return pa.Table.from_batches([batch])


def _write_table_to_sink(table: pa.Table, sink: Any) -> None:
    with pa.ipc.new_file(sink, table.schema) as writer:
        for batch in table.to_batches():
            writer.write_batch(batch)


def build_baton_buffer(state: Dict[str, Any]) -> pa.Buffer:
    """Serialize a mission state into an in-memory v2 IPC buffer (RAM cache)."""
    sink = pa.BufferOutputStream()
    _write_table_to_sink(build_baton_table(state), sink)
    return sink.getvalue()


def write_baton_v2(path, state: Dict[str, Any]) -> None:
    """Write a mission state as a Columnar Baton v2 IPC file."""
    _write_table_to_sink(build_baton_table(state), pa.OSFile(str(path), "wb"))


def _is_v2_schema(schema: pa.Schema) -> bool:
    names = schema.names or []
    return "schema_version" in names and "hist_role" in names


def _state_from_source(source: Any) -> Any:
    """Open an IPC stream/file source and return a state object (v2 or v1)."""
    try:
        reader = pa.ipc.open_file(source)
        table = reader.read_all()
    except BatonCorruptionError:
        raise
    except Exception as e:
        raise BatonCorruptionError(f"Unreadable baton Arrow payload: {e}") from e

    if table.num_rows < 1:
        raise BatonCorruptionError("Baton Arrow payload contains no rows")

    if _is_v2_schema(table.schema):
        return ColumnarBatonState(table)

    # --- v1 legacy path: materialize immediately ---
    try:
        batch = table.to_batches()[0]
        objective = batch.column(0)[0].as_py()
        run_id = batch.column(1)[0].as_py()
        history_raw = batch.column(2)[0].as_py()
        metadata_raw = batch.column(3)[0].as_py()
        history = json.loads(history_raw) if history_raw else []
        metadata = json.loads(metadata_raw) if metadata_raw else {}
        if not isinstance(history, list) or not isinstance(metadata, dict):
            raise BatonCorruptionError("Baton history/metadata JSON has wrong types")
    except BatonCorruptionError:
        raise
    except Exception as e:
        raise BatonCorruptionError(f"Corrupt legacy baton payload: {e}") from e
    return LegacyJSONState(
        {
            "objective": objective or "",
            "run_id": run_id or "",
            "history": history,
            "metadata": metadata,
        }
    )


def state_from_buffer(buffer: pa.Buffer) -> Any:
    """Read a state object from an in-memory IPC buffer (RAM cache fast path)."""
    return _state_from_source(pa.BufferReader(buffer))


def read_baton(path, mmap: bool = True) -> Any:
    """
    Read a baton file, auto-detecting v2 vs v1.

    Returns ColumnarBatonState (v2, lazy) or LegacyJSONState (v1, eager).
    Raises BatonCorruptionError for truncated/corrupt payloads.
    """
    try:
        if mmap:
            with pa.memory_map(str(path), mode="r") as source:
                return _state_from_source(source)
        with pa.OSFile(str(path), mode="rb") as source:
            return _state_from_source(source)
    except BatonCorruptionError:
        raise
    except Exception as e:
        raise BatonCorruptionError(f"Corrupt or truncated baton file {path}: {e}") from e


def _ts_from_turn_dict(turn: Dict[str, Any]) -> Optional[float]:
    for key in ("timestamp", "time", "ts"):
        raw = turn.get(key)
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw)
            except ValueError:
                continue
    return None


class ColumnarBatonState:
    """
    Lazy wrapper over a single-row v2 baton Table.

    Supports both attribute-style access (.objective, .metadata, ...) and
    legacy dict indexing (state["objective"], state["metadata"]["trace_id"])
    so existing pickup() consumers keep working unchanged.
    """

    def __init__(self, table: pa.Table):
        if not _is_v2_schema(table.schema):
            raise BatonCorruptionError(
                f"Not a v2 baton schema: {table.schema.names}"
            )
        if table.num_rows != 1:
            raise BatonCorruptionError(
                f"v2 baton must be a single row, got {table.num_rows}"
            )
        self._table = table
        self._metadata: Optional[Dict[str, Any]] = None

    # -- scalar properties -------------------------------------------------

    @property
    def objective(self) -> str:
        return self._table.column("objective")[0].as_py() or ""

    @property
    def run_id(self) -> str:
        return self._table.column("run_id")[0].as_py() or ""

    @property
    def trace_id(self) -> str:
        return self.metadata.get("trace_id", "")

    @property
    def metadata(self) -> Dict[str, Any]:
        if self._metadata is None:
            raw = self._table.column("metadata_json")[0].as_py()
            self._metadata = json.loads(raw) if raw else {}
        return self._metadata

    @property
    def schema_version(self) -> int:
        value = self._table.column("schema_version")[0].as_py()
        return int(value) if value is not None else SCHEMA_VERSION

    @property
    def created_at(self) -> Optional[datetime]:
        return self._table.column("created_at")[0].as_py()

    # -- lazy columnar history access --------------------------------------

    def _child(self, field_name: str) -> pa.Array:
        scalar = self._table.column(field_name)[0]
        if scalar is None or scalar.as_py() is None:
            return pa.array([], type=self._table.schema.field(field_name).type.value_type)
        return scalar.values

    def __bool__(self) -> bool:
        return True

    def __len__(self) -> int:
        return len(self._child("hist_role"))

    def turn(self, i: int) -> Dict[str, Any]:
        n = len(self)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise IndexError(f"turn index out of range: {i}")
        role = self._child("hist_role")[i].as_py()
        content = self._child("hist_content")[i].as_py()
        ts = self._child("hist_ts")[i].as_py()
        return {
            "role": role if role is not None else "",
            "content": content if content is not None else "",
            "ts": float(ts) if ts is not None else 0.0,
        }

    def iter_turns(self, start: int = 0, stop: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        n = len(self)
        start = max(0, start)
        stop = n if stop is None else min(stop, n)
        roles = self._child("hist_role")
        contents = self._child("hist_content")
        tss = self._child("hist_ts")
        for i in range(start, stop):
            role = roles[i].as_py()
            content = contents[i].as_py()
            ts = tss[i].as_py()
            yield {
                "role": role if role is not None else "",
                "content": content if content is not None else "",
                "ts": float(ts) if ts is not None else 0.0,
            }

    def to_list(self) -> List[Dict[str, Any]]:
        return list(self.iter_turns())

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe snapshot consumed by the activity journal (replay path)."""
        return self.to_state()

    # -- legacy-shape compatibility ----------------------------------------

    def _history_exact(self) -> List[Dict[str, Any]]:
        raw = self._table.column("history_json")[0].as_py()
        history = json.loads(raw) if raw else []
        if not isinstance(history, list):
            raise BatonCorruptionError("Baton history_json is not a list")
        return history

    def to_state(self) -> Dict[str, Any]:
        """Exact legacy pickup() dict shape (trace_id lives inside metadata)."""
        return {
            "objective": self.objective,
            "run_id": self.run_id,
            "history": self._history_exact(),
            "metadata": self.metadata,
        }

    # -- dict-compat surface ------------------------------------------------

    _KEYS = ("objective", "run_id", "history", "metadata")

    def __getitem__(self, key: str) -> Any:
        if key == "objective":
            return self.objective
        if key == "run_id":
            return self.run_id
        if key == "metadata":
            return self.metadata
        if key == "history":
            return self._history_exact()
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: object) -> bool:
        return key in self._KEYS

    def keys(self):
        return self._KEYS

    def __repr__(self) -> str:
        return (
            f"ColumnarBatonState(v{self.schema_version}, turns={len(self)}, "
            f"run_id={self.run_id!r})"
        )


class LegacyJSONState(dict):
    """
    v1 baton state: a plain dict in the exact legacy pickup shape
    ({"objective","run_id","history","metadata"}), augmented with the same
    lazy-style accessor API as ColumnarBatonState. Materializes eagerly.
    """

    @property
    def objective(self) -> str:
        return self.get("objective", "")

    @property
    def run_id(self) -> str:
        return self.get("run_id", "")

    @property
    def metadata(self) -> Dict[str, Any]:
        return self.get("metadata", {})

    @property
    def trace_id(self) -> str:
        return self.metadata.get("trace_id", "")

    @property
    def schema_version(self) -> int:
        return 1

    @property
    def created_at(self) -> Optional[datetime]:
        return None

    def __bool__(self) -> bool:
        # A loaded baton is always truthy regardless of turn count
        # (dict subclass would otherwise use history length).
        return True

    def __len__(self) -> int:
        return len(self.get("history", []))

    def turn(self, i: int) -> Dict[str, Any]:
        history = self.get("history", [])
        turn = history[i]
        ts = _ts_from_turn_dict(turn) if isinstance(turn, dict) else None
        return {
            "role": turn.get("role", "") if isinstance(turn, dict) else "",
            "content": turn.get("content", "") if isinstance(turn, dict) else str(turn),
            "ts": float(ts) if ts is not None else 0.0,
        }

    def iter_turns(self, start: int = 0, stop: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        history = self.get("history", [])
        stop = len(history) if stop is None else min(stop, len(history))
        for i in range(max(0, start), stop):
            yield self.turn(i)

    def to_list(self) -> List[Dict[str, Any]]:
        return list(self.iter_turns())

    def to_state(self) -> Dict[str, Any]:
        return dict(self)
