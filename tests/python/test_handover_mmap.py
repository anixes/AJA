import pytest
import json
import pyarrow as pa
from pathlib import Path
from agentx.runtime.handover import BatonManager

def test_baton_mmap_serialization(tmp_path):
    # Setup BatonManager with custom path
    manager = BatonManager()
    manager.baton_dir = tmp_path

    objective = "Test memory mapped baton serialization performance"
    state = {
        "run_id": "test_mmap_run_123",
        "history": [
            {"role": "user", "content": "Hello AJA"},
            {"role": "assistant", "content": "I am ready to help you zero-copy!"}
        ],
        "metadata": {
            "source": "unit_test",
            "iterations": 42
        }
    }

    # 1. Capture to Arrow Table
    code = manager.capture(objective, state)
    assert len(code) == 6

    # 2. Verify files exist
    json_path = tmp_path / f"baton_{code}.json"
    arrow_path = tmp_path / f"baton_{code}.arrow"
    assert json_path.exists()
    assert arrow_path.exists()

    # 3. Read using pyarrow.memory_map directly
    with pa.memory_map(str(arrow_path), mode="r") as source:
        reader = pa.ipc.open_file(source)
        batch = reader.read_all().to_batches()[0]
        assert batch.column(0)[0].as_py() == objective
        assert batch.column(1)[0].as_py() == state["run_id"]
        assert json.loads(batch.column(2)[0].as_py()) == state["history"]
        assert json.loads(batch.column(3)[0].as_py()) == state["metadata"]

    # 4. Pickup using BatonManager (which uses memory_map)
    thawed = manager.pickup(code)
    assert thawed is not None
    assert thawed["objective"] == objective
    assert thawed["run_id"] == state["run_id"]
    assert thawed["history"] == state["history"]
    assert thawed["metadata"] == state["metadata"]
