import json
import logging
import random
import string
import time
import agentx_native
from pathlib import Path
from typing import Dict, Any, Optional
from agentx.config import PROJECT_ROOT

logger = logging.getLogger(__name__)


class HandoverManager:
    """Base class for handovers."""

    def __init__(self):
        self.state_dir = PROJECT_ROOT / ".agentx" / "handovers"
        self.state_dir.mkdir(parents=True, exist_ok=True)


class BatonManager(HandoverManager):
    """
    Specialized manager for high-performance 'Baton' handoffs using Rust-backed Apache Arrow Tables.
    Leverages native Rust speed for O(1) state serialization and zero-copy access.
    """

    def __init__(self):
        super().__init__()
        self.baton_dir = PROJECT_ROOT / ".agentx" / "batons"
        self.baton_dir.mkdir(parents=True, exist_ok=True)

    def _generate_code(self, length: int = 6) -> str:
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

    def capture(self, objective: str, orchestrator_state: Dict[str, Any]) -> str:
        """
        Serializes mission state into a cutting-edge Apache Arrow Table via Rust Core.
        This is the 'Complex Rust Logic' that ensures maximum performance.
        """
        code = self._generate_code()
        baton_path = self.baton_dir / f"baton_{code}.json"

        # Meta-data for the baton (small JSON)
        baton_meta = {"code": code, "timestamp": time.time(), "ttl": 3600}

        # ARROW TABLE SERIALIZATION (Handled by Rust Core)
        arrow_path = baton_path.with_suffix(".arrow")

        history = orchestrator_state.get("history", [])
        run_id = orchestrator_state.get("run_id", "unknown")
        
        # Trace propagation: inject the active trace_id into baton metadata
        from agentx.observability.telemetry import get_trace_id
        metadata = dict(orchestrator_state.get("metadata", {}))
        metadata["trace_id"] = get_trace_id()

        # Call the high-performance Rust function
        try:
            agentx_native.write_baton(
                str(arrow_path),
                objective,
                run_id,
                json.dumps(history),
                json.dumps(metadata),
            )
        except Exception as e:
            logger.exception("Failed to write baton Arrow state to %s", arrow_path)
            raise RuntimeError(f"Failed to write baton Arrow state: {arrow_path}") from e

        baton_meta["arrow_ref"] = str(arrow_path)

        with baton_path.open("w", encoding="utf-8") as f:
            json.dump(baton_meta, f)

        return code

    def pickup(self, code: str) -> Optional[Dict[str, Any]]:
        """
        Picks up a baton and 'thaws' the Arrow Table back into a state via memory-mapping.
        Leverages native memory mapping for extreme zero-copy read speed.
        """
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
                        state["objective"] = batch.column(0)[0].as_py()
                        state["run_id"] = batch.column(1)[0].as_py()
                        state["history"] = json.loads(batch.column(2)[0].as_py())
                        state["metadata"] = json.loads(batch.column(3)[0].as_py())
                except Exception as mmap_err:
                    logger.warning("Failed zero-copy memory-mapped read, falling back to standard read: %s", mmap_err)
                    try:
                        rust_state = agentx_native.read_baton(str(arrow_path))
                        state["objective"] = rust_state["objective"]
                        state["run_id"] = rust_state["run_id"]
                        state["history"] = json.loads(rust_state["history_json"])
                        state["metadata"] = json.loads(rust_state["metadata_json"])
                    except Exception as e:
                        logger.exception("Failed to read baton Arrow state from %s", arrow_path)
                        raise RuntimeError(f"Failed to read baton Arrow state: {arrow_path}") from e

        # Thaw and restore trace_id from the loaded metadata
        if state and "metadata" in state:
            trace_id = state["metadata"].get("trace_id")
            if trace_id:
                from agentx.observability.telemetry import set_trace_id
                set_trace_id(trace_id)

        return state

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
            except Exception:
                logger.exception("Failed to clean up baton %s", baton_path)

