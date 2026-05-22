import pytest
import json
import base64
from unittest.mock import patch, MagicMock
from agentx.runtime.handover import BatonManager

def test_local_arrow_baton_flow():
    mgr = BatonManager()
    
    # Capture a mock baton
    objective = "Audit codebase for structural security flaws"
    state = {
        "run_id": "test-run-123",
        "history": [{"role": "user", "content": "hello"}],
        "metadata": {"test": True}
    }
    
    code = mgr.capture(objective, state)
    assert len(code) == 6
    
    # Pickup baton locally
    loaded = mgr.pickup(code)
    assert loaded is not None
    assert loaded["objective"] == objective
    assert loaded["run_id"] == "test-run-123"
    assert loaded["history"] == [{"role": "user", "content": "hello"}]
    assert loaded["metadata"]["test"] is True
    
    # Test cleanup
    mgr.cleanup_expired()

def test_remote_baton_serialization():
    mgr = BatonManager()
    
    objective = "Test remote baton fleet transmission"
    state = {
        "run_id": "remote-123",
        "history": [],
        "metadata": {"fleet": True}
    }
    
    code = mgr.capture(objective, state)
    
    # Read files to mock the payload
    baton_path = mgr.baton_dir / f"baton_{code}.json"
    arrow_path = mgr.baton_dir / f"baton_{code}.arrow"
    
    with open(baton_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    with open(arrow_path, "rb") as f:
        arrow_data = f.read()
        
    payload = {
        "code": code,
        "meta": meta,
        "arrow_data_b64": base64.b64encode(arrow_data).decode("utf-8")
    }
    
    # Create a new BatonManager instance to simulate a remote worker receiving it
    remote_mgr = BatonManager()
    remote_mgr.baton_dir = remote_mgr.baton_dir.parent / "remote_test_batons"
    remote_mgr.baton_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Receive remote baton
        rx_code = remote_mgr.receive_baton(payload)
        assert rx_code == code
        
        # Pickup remote baton natively
        loaded = remote_mgr.pickup(rx_code)
        assert loaded is not None
        assert loaded["objective"] == objective
        assert loaded["run_id"] == "remote-123"
        assert loaded["metadata"]["fleet"] is True
    finally:
        # Clean up remote test files
        import shutil
        if remote_mgr.baton_dir.exists():
            shutil.rmtree(remote_mgr.baton_dir)

@patch("urllib.request.urlopen")
def test_remote_baton_transmission(mock_urlopen):
    # Mock successful HTTP response
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    
    mgr = BatonManager()
    objective = "Transmit mission step"
    state = {
        "run_id": "tx-123",
        "history": [],
        "metadata": {}
    }
    
    code = mgr.capture(objective, state)
    
    success = mgr.transmit_baton(code, "http://remote-swarm-worker:8000/baton/receive")
    assert success is True
    assert mock_urlopen.called
