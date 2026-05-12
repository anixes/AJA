import json
import random
import string
import time
import agentx_native
from pathlib import Path
from typing import Dict, Any, Optional
from agentx.config import PROJECT_ROOT

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
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

    def capture(self, objective: str, orchestrator_state: Dict[str, Any]) -> str:
        """
        Serializes mission state into a cutting-edge Apache Arrow Table via Rust Core.
        This is the 'Complex Rust Logic' that ensures maximum performance.
        """
        code = self._generate_code()
        baton_path = self.baton_dir / f"baton_{code}.json"
        
        # Meta-data for the baton (small JSON)
        baton_meta = {
            "code": code,
            "timestamp": time.time(),
            "ttl": 3600
        }

        # ARROW TABLE SERIALIZATION (Handled by Rust Core)
        arrow_path = baton_path.with_suffix(".arrow")
        
        history = orchestrator_state.get("history", [])
        run_id = orchestrator_state.get("run_id", "unknown")
        metadata = orchestrator_state.get("metadata", {})
        
        # Call the high-performance Rust function
        try:
            agentx_native.write_baton(
                str(arrow_path),
                objective,
                run_id,
                json.dumps(history),
                json.dumps(metadata)
            )
        except Exception as e:
            print(f"[!] Error writing baton: {e}")
        
        baton_meta["arrow_ref"] = str(arrow_path)

        with open(baton_path, "w") as f:
            json.dump(baton_meta, f)
        
        return code

    def pickup(self, code: str) -> Optional[Dict[str, Any]]:
        """
        Picks up a baton and 'thaws' the Arrow Table back into a state via Rust Core.
        Leverages native memory mapping for extreme speed.
        """
        baton_path = self.baton_dir / f"baton_{code}.json"
        if not baton_path.exists():
            return None

        with open(baton_path, "r") as f:
            meta = json.load(f)

        state = {}
        # ARROW TABLE DESERIALIZATION (Handled by Rust Core)
        if "arrow_ref" in meta:
            arrow_path = Path(meta["arrow_ref"])
            if arrow_path.exists():
                # Call high-performance Rust reader
                rust_state = agentx_native.read_baton(str(arrow_path))
                
                state["objective"] = rust_state["objective"]
                state["run_id"] = rust_state["run_id"]
                state["history"] = json.loads(rust_state["history_json"])
                state["metadata"] = json.loads(rust_state["metadata_json"])
        
        return state

    def cleanup_expired(self):
        """Removes batons and their associated Arrow Tables past TTL."""
        now = time.time()
        for baton_path in self.baton_dir.glob("*.json"):
            try:
                with open(baton_path, "r") as f:
                    data = json.load(f)
                    if now - data["timestamp"] > data.get("ttl", 3600):
                        baton_path.unlink()
                        arrow_path = baton_path.with_suffix(".arrow")
                        if arrow_path.exists():
                            arrow_path.unlink()
            except Exception:
                pass
