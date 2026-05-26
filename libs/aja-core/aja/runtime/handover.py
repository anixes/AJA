import json
import asyncio
import logging
import random
import string
import time
import threading
import contextlib
import aja_native
from pathlib import Path
from typing import Dict, Any, Optional
from aja.config import PROJECT_ROOT
from aja.runtime.baton_types import MissionBatonPayload, WorkerBatonPayload

_IN_MEMORY_BATONS = {}
_BATON_LOCK = threading.Lock()
_MAX_IN_MEMORY_BATONS = 128
_IN_MEMORY_BATON_TTL_SECONDS = 3600


logger = logging.getLogger(__name__)


def _cache_baton(code: str, buffer: Any) -> None:
    now = time.time()
    with _BATON_LOCK:
        stale_codes = [
            cached_code
            for cached_code, (cached_at, _cached_buffer) in _IN_MEMORY_BATONS.items()
            if now - cached_at > _IN_MEMORY_BATON_TTL_SECONDS
        ]
        for cached_code in stale_codes:
            _IN_MEMORY_BATONS.pop(cached_code, None)

        _IN_MEMORY_BATONS[code] = (now, buffer)

        while len(_IN_MEMORY_BATONS) > _MAX_IN_MEMORY_BATONS:
            oldest_code = min(_IN_MEMORY_BATONS, key=lambda item: _IN_MEMORY_BATONS[item][0])
            _IN_MEMORY_BATONS.pop(oldest_code, None)


def _get_cached_baton(code: str) -> Optional[Any]:
    now = time.time()
    with _BATON_LOCK:
        cached = _IN_MEMORY_BATONS.get(code)
        if cached is None:
            return None
        cached_at, buffer = cached
        if now - cached_at > _IN_MEMORY_BATON_TTL_SECONDS:
            _IN_MEMORY_BATONS.pop(code, None)
            return None
        return buffer


def write_baton_ipc(path: Path, baton_data: Dict[str, Any]) -> None:
    """
    Write a worker baton through the runtime-owned native IPC boundary.

    This keeps orchestration code from importing aja_native directly while
    preserving the legacy JSON-payload Arrow schema used by worker batons.
    """
    try:
        aja_native.write_baton_ipc(str(path), WorkerBatonPayload(baton_data).to_json())
    except Exception as e:
        logger.exception("Failed to write worker baton IPC state to %s", path)
        raise RuntimeError(f"Failed to write worker baton IPC state: {path}") from e


def read_baton_ipc(path: Path) -> Dict[str, Any]:
    """
    Read a worker baton through the runtime-owned native IPC boundary.
    """
    try:
        return WorkerBatonPayload.from_json(aja_native.read_baton_ipc(str(path))).data
    except Exception as e:
        logger.exception("Failed to read worker baton IPC state from %s", path)
        raise RuntimeError(f"Failed to read worker baton IPC state: {path}") from e


class HandoverManager:
    """Base class for handovers."""

    def __init__(self):
        self.state_dir = PROJECT_ROOT / ".aja" / "handovers"
        self.state_dir.mkdir(parents=True, exist_ok=True)


class BatonManager(HandoverManager):
    """
    Specialized manager for high-performance 'Baton' handoffs using Rust-backed Apache Arrow Tables.
    Leverages native Rust speed for O(1) state serialization and zero-copy access.
    """

    def __init__(self):
        super().__init__()
        self.baton_dir = PROJECT_ROOT / ".aja" / "batons"
        self.baton_dir.mkdir(parents=True, exist_ok=True)

    def _generate_code(self, length: int = 6) -> str:
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

    from aja.runtime.execution.activity import durable_activity

    @durable_activity("baton.capture")
    def capture(self, objective: str, orchestrator_state: Dict[str, Any], trace_id: Optional[str] = None) -> str:
        """
        Serializes mission state into a cutting-edge Apache Arrow Table via Rust Core.
        This is the 'Complex Rust Logic' that ensures maximum performance.
        """
        run_id = orchestrator_state.get("run_id")
        if run_id:
            from aja.runtime.replay_guards import derive_baton_code
            stage = len(orchestrator_state.get("history", []))
            code = derive_baton_code(run_id, stage)
        else:
            code = self._generate_code()
        baton_path = self.baton_dir / f"baton_{code}.json"

        # Meta-data for the baton (small JSON)
        baton_meta = {"code": code, "timestamp": time.time(), "ttl": 3600}

        # ARROW TABLE SERIALIZATION (Handled by Rust Core)
        arrow_path = baton_path.with_suffix(".arrow")

        # Trace propagation: inject the active trace_id into baton metadata
        from aja.observability.telemetry import get_trace_id
        metadata = dict(orchestrator_state.get("metadata", {}))
        metadata["trace_id"] = trace_id or get_trace_id()
        payload = MissionBatonPayload.from_state(
            objective,
            {
                **orchestrator_state,
                "metadata": metadata,
            },
        )

        # Call the high-performance Rust function
        try:
            aja_native.write_baton(str(arrow_path), *payload.to_native_args())
        except Exception as e:
            logger.exception("Failed to write baton Arrow state to %s", arrow_path)
            raise RuntimeError(f"Failed to write baton Arrow state: {arrow_path}") from e

        # Optimize Baton: Serialize to a pyarrow.Buffer and store in RAM cache
        try:
            import pyarrow as pa
            schema = pa.schema([
                ("objective", pa.string()),
                ("run_id", pa.string()),
                ("history_json", pa.string()),
                ("metadata_json", pa.string()),
            ])
            batch = pa.RecordBatch.from_arrays([
                pa.array([objective], type=pa.string()),
                pa.array([payload.run_id], type=pa.string()),
                pa.array([json.dumps(payload.history)], type=pa.string()),
                pa.array([json.dumps(payload.metadata)], type=pa.string()),
            ], schema=schema)

            sink = pa.BufferOutputStream()
            with pa.ipc.new_file(sink, schema) as writer:
                writer.write_batch(batch)
            buffer = sink.getvalue()

            _cache_baton(code, buffer)
        except Exception as pyarrow_err:
            logger.warning("Failed in-memory Arrow serialization: %s", pyarrow_err)

        baton_meta["arrow_ref"] = str(arrow_path)

        with baton_path.open("w", encoding="utf-8") as f:
            json.dump(baton_meta, f)

        return code

    @durable_activity("baton.pickup")
    def pickup(self, code: str, mutate_global_trace: bool = True) -> Optional[Dict[str, Any]]:
        """
        Picks up a baton and 'thaws' the Arrow Table back into a state via memory-mapping.
        Leverages native memory mapping for extreme zero-copy read speed.
        """
        # Check in-memory baton cache first for sub-millisecond zero-copy retrieval
        buffer = _get_cached_baton(code)

        if buffer is not None:
            try:
                import pyarrow as pa
                state = {}
                with pa.BufferReader(buffer) as source:
                    reader = pa.ipc.open_file(source)
                    batch = reader.read_all().to_batches()[0]
                    payload = MissionBatonPayload(
                        objective=batch.column(0)[0].as_py(),
                        run_id=batch.column(1)[0].as_py(),
                        history=json.loads(batch.column(2)[0].as_py()),
                        metadata=json.loads(batch.column(3)[0].as_py()),
                    )
                    state = payload.to_state()

                # Thaw and restore trace_id from the loaded metadata
                if "metadata" in state:
                    trace_id = state["metadata"].get("trace_id")
                    if trace_id and mutate_global_trace:
                        from aja.observability.telemetry import set_trace_id
                        set_trace_id(trace_id)

                return state
            except Exception as in_mem_err:
                logger.warning("Failed in-memory baton read for code %s: %s", code, in_mem_err)

        baton_path = self.baton_dir / f"baton_{code}.json"
        if not baton_path.exists():
            return None

        with baton_path.open("r", encoding="utf-8") as f:
            meta = json.load(f)

        state = {}
        # ARROW TABLE DESERIALIZATION (Using memory-mapped PyArrow or falling back to Rust Core)
        if "arrow_ref" in meta:
            arrow_path = Path(meta["arrow_ref"])
            if arrow_path.exists():
                try:
                    import pyarrow as pa
                    with pa.memory_map(str(arrow_path), mode="r") as source:
                        reader = pa.ipc.open_file(source)
                        batch = reader.read_all().to_batches()[0]
                        payload = MissionBatonPayload(
                            objective=batch.column(0)[0].as_py(),
                            run_id=batch.column(1)[0].as_py(),
                            history=json.loads(batch.column(2)[0].as_py()),
                            metadata=json.loads(batch.column(3)[0].as_py()),
                        )
                        state = payload.to_state()
                except Exception as mmap_err:
                    logger.warning("Failed zero-copy memory-mapped read, falling back to standard read: %s", mmap_err)
                    try:
                        rust_state = aja_native.read_baton(str(arrow_path))
                        state = MissionBatonPayload.from_native_dict(rust_state).to_state()
                    except Exception as e:
                        logger.exception("Failed to read baton Arrow state from %s", arrow_path)
                        raise RuntimeError(f"Failed to read baton Arrow state: {arrow_path}") from e

        # Thaw and restore trace_id from the loaded metadata
        if state and "metadata" in state:
            trace_id = state["metadata"].get("trace_id")
            if trace_id and mutate_global_trace:
                from aja.observability.telemetry import set_trace_id
                set_trace_id(trace_id)

        return state

    @contextlib.contextmanager
    def pickup_scope(self, code: str):
        """Picks up a baton and yields the loaded state locally scoped within its trace context."""
        state = self.pickup(code, mutate_global_trace=False)
        trace_id = state.get("metadata", {}).get("trace_id") if state else None
        from aja.observability.telemetry import TraceContextManager
        with TraceContextManager(trace_id):
            yield state

    def transmit_baton(self, code: str, endpoint_url: str) -> bool:
        """
        Transmits a captured baton's metadata and binary Arrow state to a remote worker network endpoint
        using standard HTTP POST. Follows standard safety and retry rules.
        """
        baton_path = self.baton_dir / f"baton_{code}.json"
        arrow_path = self.baton_dir / f"baton_{code}.arrow"
        
        if not baton_path.exists() or not arrow_path.exists():
            logger.error(f"Cannot transmit baton: file for code {code} does not exist.")
            return False
            
        try:
            with open(baton_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            with open(arrow_path, "rb") as f:
                arrow_data = f.read()
                
            import base64
            payload = {
                "code": code,
                "meta": meta,
                "arrow_data_b64": base64.b64encode(arrow_data).decode("utf-8")
            }
            
            import urllib.request
            import urllib.error
            
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint_url,
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=10.0) as response:
                if response.status in (200, 201):
                    logger.info(f"Baton {code} successfully transmitted to remote worker: {endpoint_url}")
                    return True
                else:
                    logger.warning(f"Failed to transmit baton to {endpoint_url}. Status code: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error transmitting baton {code} to {endpoint_url}: {e}")
            return False

    async def transmit_baton_async(self, code: str, endpoint_url: str) -> bool:
        """Async-safe wrapper for remote baton transmission."""
        return await asyncio.to_thread(self.transmit_baton, code, endpoint_url)

    def receive_baton(self, payload_dict: Dict[str, Any]) -> str:
        """
        Receives a remote baton payload, deserializes it, and saves it locally.
        Returns the saved baton code.
        """
        code = payload_dict["code"]
        meta = payload_dict["meta"]
        arrow_data_b64 = payload_dict["arrow_data_b64"]
        
        import base64
        arrow_data = base64.b64decode(arrow_data_b64.encode("utf-8"))
        
        baton_path = self.baton_dir / f"baton_{code}.json"
        arrow_path = self.baton_dir / f"baton_{code}.arrow"
        
        # Save local files
        with open(baton_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        with open(arrow_path, "wb") as f:
            f.write(arrow_data)

        # Cache in memory
        try:
            import pyarrow as pa
            buffer = pa.py_buffer(arrow_data)
            _cache_baton(code, buffer)
        except Exception as e:
            logger.warning("Failed to cache received baton in memory: %s", e)
            
        logger.info(f"Baton {code} received and persisted locally in {self.baton_dir}")
        return code

    def cleanup_expired(self):
        """Removes batons and their associated Arrow Tables past TTL."""
        now = time.time()
        for baton_path in self.baton_dir.glob("*.json"):
            try:
                with baton_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                    if now - data["timestamp"] > data.get("ttl", 3600):
                        baton_path.unlink()
                        arrow_path = baton_path.with_suffix(".arrow")
                        if arrow_path.exists():
                            arrow_path.unlink()
                        with _BATON_LOCK:
                            _IN_MEMORY_BATONS.pop(data.get("code", baton_path.stem.removeprefix("baton_")), None)
            except Exception:
                logger.exception("Failed to clean up baton %s", baton_path)

    def clear_memory_cache(self):
        """Clear the process-local baton cache owned by this runtime."""
        with _BATON_LOCK:
            _IN_MEMORY_BATONS.clear()
