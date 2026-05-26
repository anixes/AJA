import hashlib
import struct
from typing import Any, Optional

def replay_safe_random(run_id: str, attempt: int, salt: str = "") -> float:
    """Deterministic float in [0,1) derived from execution identity."""
    data = f"{run_id}:{attempt}:{salt}".encode("utf-8")
    digest = hashlib.sha256(data).digest()
    val = struct.unpack(">Q", digest[:8])[0]
    return val / (2**64)

def derive_run_id(mission_id: str, attempt: int) -> str:
    h = hashlib.sha256(f"run:{mission_id}:{attempt}".encode("utf-8")).hexdigest()
    return f"run-{h[:16]}"

def derive_session_id(run_id: str, node_id: str) -> str:
    h = hashlib.sha256(f"sess:{run_id}:{node_id}".encode("utf-8")).hexdigest()
    return f"exec-{h[:16]}"

def derive_baton_code(run_id: str, stage: int) -> str:
    h = hashlib.sha256(f"baton:{run_id}:{stage}".encode("utf-8")).hexdigest()
    return h[:6].upper()

def derive_activity_id(session_id: str, sequence: int, name: str) -> str:
    h = hashlib.sha256(f"act:{session_id}:{sequence}:{name}".encode("utf-8")).hexdigest()
    return f"act-{h[:12]}"

def derive_trace_id(run_id: str) -> str:
    h = hashlib.sha256(f"trace:{run_id}".encode("utf-8")).hexdigest()
    return f"tr-{h[:16]}"

def assert_managed_context_purity(run_id: str) -> None:
    """Assert that the current execution context is determinism-compliant."""
    import os
    if os.getenv("AJA_REPLAY_ASSERTIONS") == "1":
        from aja.runtime.execution.activity import get_activity_context
        ctx = get_activity_context()
        assert ctx is not None, "Managed execution path has no ActivityContext set"
