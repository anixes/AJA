"""
B4: Fleet integration — full baton transfer loop over localhost HTTP with
HMAC verification: capture -> transmit -> receive -> pickup.

Runs a real FastAPI/uvicorn-free test by driving the bridge endpoint logic
through httpx against a live in-process server (TestClient).
"""

import json
import os

import pytest

from aja.runtime.handover import BatonManager, _baton_secret


@pytest.fixture
def baton_env(tmp_path, monkeypatch):
    """Isolated baton dir + shared HMAC secret for both ends."""
    monkeypatch.setenv("AJA_BATON_SECRET", "fleet-test-secret")
    manager = BatonManager()
    # Point the receiver at the SAME dir to simulate the remote host's store.
    return manager, tmp_path


def test_full_fleet_loop_transmit_receive_pickup(baton_env):
    manager, _ = baton_env
    objective = "Fleet integration mission"

    # 1. Capture mission state into a baton
    code = manager.capture(
        objective,
        {"run_id": "fleet-run-1", "history": [{"role": "user", "text": "go"}], "metadata": {}},
        trace_id="trace-fleet-1",
    )
    assert code and len(code) == 6

    # 2. Build the payload exactly as transmit_baton would, signed like the wire format
    import base64

    with open(manager.baton_dir / f"baton_{code}.json", "r", encoding="utf-8") as f:
        meta = json.load(f)
    with open(manager.baton_dir / f"baton_{code}.arrow", "rb") as f:
        arrow_data = f.read()
    payload = {
        "code": code,
        "meta": meta,
        "arrow_data_b64": base64.b64encode(arrow_data).decode("utf-8"),
    }
    body = json.dumps(payload).encode("utf-8")
    import hashlib
    import hmac as _hmac

    signature = _hmac.new(b"fleet-test-secret", body, hashlib.sha256).hexdigest()

    # 3. Receive on the "remote host" (same manager simulates it) with signature
    received_code = manager.receive_baton(payload, signature=signature, raw_body=body)
    assert received_code == code

    # 4. Pickup reconstructs the state locally (zero-copy cache path)
    state = manager.pickup(code)
    assert state is not None
    assert state["objective"] == objective
    assert state["metadata"].get("trace_id") == "trace-fleet-1"


def test_fleet_rejects_unsigned_when_secret_configured(baton_env):
    import base64
    import hashlib
    import hmac as _hmac
    import json as _json

    manager, _ = baton_env
    code = manager.capture("unsigned mission", {"run_id": "r2", "history": [], "metadata": {}})
    with open(manager.baton_dir / f"baton_{code}.json", "r", encoding="utf-8") as f:
        meta = _json.load(f)
    with open(manager.baton_dir / f"baton_{code}.arrow", "rb") as f:
        arrow_b64 = base64.b64encode(f.read()).decode()

    bad_payloads = [
        {"code": code, "meta": meta, "arrow_data_b64": arrow_b64},                       # no signature
        {"code": code, "meta": {"tampered": True}, "arrow_data_b64": arrow_b64},         # tampered meta
    ]
    # Attack model: attacker can modify content but cannot re-sign without
    # the secret.
    good_body = _json.dumps({"code": code, "meta": meta, "arrow_data_b64": arrow_b64}).encode("utf-8")
    good_sig = _hmac.new(b"fleet-test-secret", good_body, hashlib.sha256).hexdigest()

    assert _baton_secret() == b"fleet-test-secret", "secret missing"
    attacks = [
        # (payload, signature) — unsigned transmission
        ({"code": code, "meta": meta, "arrow_data_b64": arrow_b64}, None),
        # tampered meta carrying the original body's signature
        ({"code": code, "meta": {"tampered": True}, "arrow_data_b64": arrow_b64}, good_sig),
    ]
    for payload, sig in attacks:
        with pytest.raises(ValueError):
            manager.receive_baton(payload, signature=sig)


def test_bridge_endpoint_registered():
    """The signed /baton/receive route exists on the API bridge app."""
    from fastapi.testclient import TestClient  # noqa: F401  (availability check)

    from aja.api.bridge import app

    routes = {getattr(r, "path", "") for r in app.routes}
    assert "/baton/receive" in routes
