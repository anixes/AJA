import os
import uuid
import pytest
from pathlib import Path

from aja.runtime.handover import write_baton_ipc, read_baton_ipc

def test_cross_layer_ipc_compatibility(tmp_path: Path):
    """
    Test explicitly that the Python WorkerBatonPayload can be safely 
    written and read through the native Rust IPC boundary.
    This guarantees Arrow 58.0.0 (Rust) and the current PyArrow align.
    """
    ipc_file = tmp_path / f"test_worker_baton_{uuid.uuid4().hex}.arrow"
    
    # Example payload data that represents what a worker baton looks like
    test_data = {
        "agent_id": "agent_alpha",
        "objective": "Scan local system logs",
        "tools_allowed": ["search", "read_file", "execute_command"],
        "metadata": {
            "trace_id": "trace-12345",
            "dry_run": True
        }
    }
    
    # 1. Write through native IPC
    try:
        write_baton_ipc(ipc_file, test_data)
    except Exception as e:
        pytest.fail(f"Failed to write IPC baton: {e}")
        
    assert ipc_file.exists(), "IPC baton file was not created on disk."
    
    # 2. Read through native IPC
    try:
        recovered_data = read_baton_ipc(ipc_file)
    except Exception as e:
        pytest.fail(f"Failed to read IPC baton: {e}")
        
    # 3. Verify fidelity
    assert recovered_data["agent_id"] == "agent_alpha"
    assert recovered_data["objective"] == "Scan local system logs"
    assert "execute_command" in recovered_data["tools_allowed"]
    assert recovered_data["metadata"]["trace_id"] == "trace-12345"
    assert recovered_data["metadata"]["dry_run"] is True
