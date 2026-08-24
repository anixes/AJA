"""Tests for the generic webhook trigger endpoint (/api/v1/trigger)."""

import pytest
from fastapi.testclient import TestClient

import aja.api.bridge as bridge
from aja.api.bridge import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(bridge, "API_TOKEN", "test-token-xyz")
    bridge._trigger_counters.clear()

    created = []

    class FakeMemory:
        def create_mission(self, goal_text):
            mission_id = f"m-{len(created) + 1}"
            created.append(goal_text)
            return {"mission_id": mission_id}

    monkeypatch.setattr(bridge, "get_aja_memory", lambda: FakeMemory())

    with TestClient(app) as tc:
        tc.created = created  # type: ignore[attr-defined]
        yield tc


AUTH = {"Authorization": "Bearer test-token-xyz"}


def test_valid_trigger_creates_mission(client):
    resp = client.post(
        "/api/v1/trigger",
        json={"goal": "Deploy the service", "source": "ci"},
        headers=AUTH,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "dispatched"
    assert data["mission_id"]
    assert client.created == ["Deploy the service"]


def test_missing_goal_returns_422(client):
    resp = client.post("/api/v1/trigger", json={"source": "ci"}, headers=AUTH)
    assert resp.status_code == 422

    resp = client.post("/api/v1/trigger", json={"goal": "   "}, headers=AUTH)
    assert resp.status_code == 422
    assert not client.created


def test_rate_limit_exceeded_returns_429(client):
    for i in range(10):
        resp = client.post(
            "/api/v1/trigger",
            json={"goal": f"task {i}", "source": "bursty"},
            headers=AUTH,
        )
        assert resp.status_code == 202, f"request {i} should pass"

    resp = client.post(
        "/api/v1/trigger",
        json={"goal": "one too many", "source": "bursty"},
        headers=AUTH,
    )
    assert resp.status_code == 429

    # A different source is still allowed (per-source limiting).
    resp = client.post(
        "/api/v1/trigger",
        json={"goal": "other source", "source": "calm"},
        headers=AUTH,
    )
    assert resp.status_code == 202


def test_invalid_token_rejected(client):
    resp = client.post(
        "/api/v1/trigger",
        json={"goal": "nope"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code in (401, 403)

    resp = client.post("/api/v1/trigger", json={"goal": "nope"})
    assert resp.status_code in (401, 403)
    assert not client.created


def test_source_defaults_to_client_ip(client):
    resp = client.post("/api/v1/trigger", json={"goal": "ip-sourced"}, headers=AUTH)
    assert resp.status_code == 202
    # TestClient requests come from the "testclient" host by default.
    assert "testclient" in bridge._trigger_counters
