import hashlib
import hmac
import json
import os
import asyncio
import logging
import secrets
import re
import string
import time
import threading
import contextlib
try:
    from aja import aja_native
except ImportError:
    try:
        import aja_native
    except ImportError:
        aja_native = None
from pathlib import Path
from typing import Dict, Any, Optional
from aja.config import PROJECT_ROOT, DATA_DIR
from aja.runtime.baton_types import MissionBatonPayload, WorkerBatonPayload

_IN_MEMORY_BATONS = {}
_BATON_LOCK = threading.Lock()
_MAX_IN_MEMORY_BATONS = 128
_IN_MEMORY_BATON_TTL_SECONDS = 3600


logger = logging.getLogger(__name__)

_BATON_CODE_PATTERN = re.compile(r"^[A-Z0-9]{6}$")
_MAX_RECEIVED_BATON_BYTES = 10 * 1024 * 1024


def _baton_secret() -> bytes:
    """Shared HMAC secret used to authenticate baton transfers between hosts."""
    return os.getenv("AJA_BATON_SECRET", "").encode("utf-8")


def _sign_payload(body: bytes) -> str:
    return hmac.new(_baton_secret(), body, hashlib.sha256).hexdigest()


def _is_local_endpoint(endpoint_url: str) -> bool:
    lowered = endpoint_url.lower()
    return any(
        marker in lowered
        for marker in ("//localhost", "//127.0.0.1", "//[::1]")
    )


def _validate_code(code: str) -> str:
    """Validates a baton code to prevent path traversal via interpolated filenames."""
    if not isinstance(code, str) or not _BATON_CODE_PATTERN.match(code):
        raise ValueError(f"Invalid baton code: {code!r}")
    return code


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
        if aja_native and hasattr(aja_native, "write_baton_ipc"):
            aja_native.write_baton_ipc(str(path), WorkerBatonPayload(baton_data).to_json())
        else:
            import pyarrow as pa
            payload_json = WorkerBatonPayload(baton_data).to_json()
            schema = pa.schema([("payload", pa.string())])
            batch = pa.RecordBatch.from_arrays([pa.array([payload_json], type=pa.string())], schema=schema)
            with pa.OSFile(str(path), "wb") as sink:
                with pa.ipc.new_file(sink, schema) as writer:
                    writer.write_batch(batch)
    except Exception as e:
        logger.exception("Failed to write worker baton IPC state to %s", path)
        raise RuntimeError(f"Failed to write worker baton IPC state: {path}") from e


def read_baton_ipc(path: Path, use_native: bool = True) -> Dict[str, Any]:
    """
    Read a worker baton through the runtime-owned native IPC boundary.

    Pass ``use_native=False`` to force the pure-pyarrow recovery path
    (used by agents/worker.py when the native reader fails or panics).
    """
    try:
        if use_native and aja_native and hasattr(aja_native, "read_baton_ipc"):
            return WorkerBatonPayload.from_json(aja_native.read_baton_ipc(str(path))).data
        import pyarrow as pa
        with pa.memory_map(str(path), mode="r") as source:
            reader = pa.ipc.open_file(source)
            batch = reader.read_all().to_batches()[0]
            raw_json = batch.column(0)[0].as_py()
            return WorkerBatonPayload.from_json(raw_json).data
    except Exception as e:
        logger.exception("Failed to read worker baton IPC state from %s", path)
        raise RuntimeError(f"Failed to read worker baton IPC state: {path}") from e


class HandoverManager:
    """Base class for handovers."""

    def __init__(self):
        self.state_dir = DATA_DIR / "handovers"
        self.state_dir.mkdir(parents=True, exist_ok=True)


class BatonManager(HandoverManager):
    """
    Specialized manager for high-performance 'Baton' handoffs using Rust-backed Apache Arrow Tables.
    Leverages native Rust speed for O(1) state serialization and zero-copy access.
    """

    def __init__(self):
        super().__init__()
        self.baton_dir = DATA_DIR / "batons"
        self.baton_dir.mkdir(parents=True, exist_ok=True)

    def _generate_code(self, length: int = 6) -> str:
        return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))

    from aja.runtime.execution.activity import durable_activity

    @durable_activity("baton.capture")
    def capture(self, objective: str, orchestrator_state: Dict[str, Any], trace_id: Optional[str] = None) -> str:
        """
        Serializes mission state into an Apache Arrow baton file.

        Writes Columnar Baton Schema v2 by default (lazy per-turn list columns);
        set AJA_BATON_SCHEMA=1 to force the legacy single-JSON-cell layout.
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

        # ARROW TABLE SERIALIZATION (pyarrow-only mission path)
        arrow_path = baton_path.with_suffix(".arrow")

        # Trace propagation: inject the active trace_id into baton metadata
        from aja.observability.telemetry import get_trace_id
        metadata = dict(orchestrator_state.get("metadata", {}))
        metadata["trace_id"] = trace_id or get_trace_id()
        state = {
            **orchestrator_state,
            "objective": objective,
            "metadata": metadata,
        }

        schema_mode = (os.getenv("AJA_BATON_SCHEMA", "2") or "2").strip()
        try:
            import pyarrow as pa
            if schema_mode == "1":
                payload = MissionBatonPayload.from_state(objective, state)
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
                with pa.OSFile(str(arrow_path), "wb") as sink:
                    with pa.ipc.new_file(sink, schema) as writer:
                        writer.write_batch(batch)

                sink_buf = pa.BufferOutputStream()
                with pa.ipc.new_file(sink_buf, schema) as writer:
                    writer.write_batch(batch)
                buffer = sink_buf.getvalue()
            else:
                from aja.runtime.baton_state import build_baton_buffer, write_baton_v2
                write_baton_v2(arrow_path, state)
                buffer = build_baton_buffer(state)
        except Exception as e:
            logger.exception("Failed to write baton Arrow state to %s", arrow_path)
            raise RuntimeError(f"Failed to write baton Arrow state: {arrow_path}") from e

        # Optimize Baton: store serialized IPC buffer in RAM cache
        try:
            _cache_baton(code, buffer)
        except Exception as cache_err:
            logger.warning("Failed in-memory Arrow caching: %s", cache_err)

        baton_meta["arrow_ref"] = str(arrow_path)

        with baton_path.open("w", encoding="utf-8") as f:
            json.dump(baton_meta, f)

        return code

    @durable_activity("baton.pickup")
    def pickup(self, code: str, mutate_global_trace: bool = True) -> Optional[Any]:
        """
        Picks up a baton and 'thaws' the Arrow Table back into a state.

        v2 batons return a lazy ColumnarBatonState (dict-index compatible);
        v1 batons return a LegacyJSONState (a plain dict in the exact legacy
        pickup shape). Both expose ["objective"], ["metadata"]["trace_id"], etc.
        """
        _validate_code(code)

        # Check in-memory baton cache first for sub-millisecond zero-copy retrieval
        buffer = _get_cached_baton(code)
        state_obj = None

        if buffer is not None:
            try:
                from aja.runtime.baton_state import state_from_buffer
                state_obj = state_from_buffer(buffer)
            except Exception as in_mem_err:
                logger.warning("Failed in-memory baton read for code %s: %s", code, in_mem_err)

        if state_obj is None:
            baton_path = self.baton_dir / f"baton_{code}.json"
            if not baton_path.exists():
                return None

            with baton_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)

            # ARROW TABLE DESERIALIZATION (memory-mapped pyarrow read)
            if "arrow_ref" in meta:
                arrow_path = Path(meta["arrow_ref"]).resolve()
                try:
                    arrow_path.relative_to(self.baton_dir.resolve())
                except ValueError:
                    logger.error("Rejected baton %s: arrow_ref %s escapes baton directory", code, arrow_path)
                    return None
                if arrow_path.exists():
                    from aja.runtime.baton_state import BatonCorruptionError, read_baton
                    try:
                        state_obj = read_baton(arrow_path)
                    except BatonCorruptionError:
                        raise
                    except Exception as mmap_err:
                        logger.warning(
                            "Failed zero-copy memory-mapped read, falling back to standard read: %s",
                            mmap_err,
                        )
                        try:
                            state_obj = read_baton(arrow_path, mmap=False)
                        except BatonCorruptionError as e:
                            logger.exception("Failed to read baton Arrow state from %s", arrow_path)
                            raise RuntimeError(f"Failed to read baton Arrow state: {arrow_path}") from e

        if state_obj is not None:
            # Thaw and restore trace_id from the loaded metadata
            trace_id = state_obj.metadata.get("trace_id")
            if trace_id and mutate_global_trace:
                from aja.observability.telemetry import set_trace_id
                set_trace_id(trace_id)

        return state_obj

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
        _validate_code(code)
        baton_path = self.baton_dir / f"baton_{code}.json"
        arrow_path = self.baton_dir / f"baton_{code}.arrow"

        if not endpoint_url.lower().startswith("https://") and not _is_local_endpoint(endpoint_url):
            logger.error(
                "Refusing to transmit baton %s over insecure transport to non-local endpoint: %s",
                code, endpoint_url,
            )
            return False

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
            headers = {"Content-Type": "application/json"}
            if _baton_secret():
                headers["X-AJA-Signature"] = _sign_payload(req_data)
            req = urllib.request.Request(
                endpoint_url,
                data=req_data,
                headers=headers,
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

    def receive_baton(
        self,
        payload_dict: Dict[str, Any],
        signature: Optional[str] = None,
        raw_body: Optional[bytes] = None,
    ) -> str:
        """
        Receives a remote baton payload, deserializes it, and saves it locally.
        Returns the saved baton code.

        When AJA_BATON_SECRET is configured on the receiving host, a valid
        HMAC-SHA256 signature over the raw request body must be supplied.
        """
        expected_secret = _baton_secret()
        if expected_secret:
            body = raw_body if raw_body is not None else json.dumps(payload_dict).encode("utf-8")
            if not signature or not hmac.compare_digest(signature, _sign_payload(body)):
                logger.error("Rejected baton: missing or invalid HMAC signature")
                raise ValueError("Invalid baton signature")

        code = _validate_code(payload_dict["code"])
        meta = payload_dict["meta"]
        arrow_data_b64 = payload_dict["arrow_data_b64"]

        import base64
        if len(arrow_data_b64) > (_MAX_RECEIVED_BATON_BYTES // 3) * 4:
            raise ValueError("Rejected baton: payload exceeds maximum allowed size")
        arrow_data = base64.b64decode(arrow_data_b64.encode("utf-8"), validate=True)
        if len(arrow_data) > _MAX_RECEIVED_BATON_BYTES:
            raise ValueError("Rejected baton: decoded payload exceeds maximum allowed size")
        
        baton_path = self.baton_dir / f"baton_{code}.json"
        arrow_path = self.baton_dir / f"baton_{code}.arrow"
        
        # Save local files. Rewrite arrow_ref to THIS host's copy: the sender's
        # absolute path escapes our baton_dir and would be rejected by the
        # pickup boundary check once the RAM cache entry expires.
        meta = dict(meta)
        meta["arrow_ref"] = str(arrow_path)
        # Atomic ordering: write the .arrow payload first, then the meta that
        # makes it pickup-able. A crash mid-way can never leave meta pointing
        # at a missing Arrow file.
        with open(arrow_path, "wb") as f:
            f.write(arrow_data)
        try:
            with open(baton_path, "w", encoding="utf-8") as f:
                json.dump(meta, f)
        except Exception:
            arrow_path.unlink(missing_ok=True)
            raise

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
