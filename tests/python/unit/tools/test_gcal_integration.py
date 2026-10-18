"""Unit tests for the Google Calendar integration (aja.calendar)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import aja.calendar.auth as gcal_auth
import aja.calendar.events as gcal_events
import aja.calendar.graph_sync as gcal_graph_sync
from aja.cognitive.temporal_graph import BiTemporalEntityGraph
from aja.config_schema import AJAConfig, GoogleCalendarSettings


def _fake_service(items):
    service = MagicMock()
    response = {"items": items}
    service.events.return_value.list.return_value.execute.return_value = response
    return service


def _sample_item():
    return {
        "id": "evt-1",
        "summary": "Standup",
        "start": {"dateTime": "2026-08-24T09:00:00Z"},
        "end": {"dateTime": "2026-08-24T09:15:00Z"},
        "location": "Room 4",
        "description": "daily sync",
        "htmlLink": "https://calendar.google.com/link",
    }


# ---------------------------------------------------------------------------
# events.list_events normalization
# ---------------------------------------------------------------------------


def test_list_events_normalizes_fields(monkeypatch):
    service = _fake_service([_sample_item()])
    monkeypatch.setattr(gcal_events, "get_service", lambda: service)

    result = gcal_events.list_events(
        "2026-08-24T00:00:00Z", "2026-08-25T00:00:00Z"
    )

    assert len(result) == 1
    event = result[0]
    assert event["id"] == "evt-1"
    assert event["title"] == "Standup"
    assert event["start_iso"] == "2026-08-24T09:00:00Z"
    assert event["end_iso"] == "2026-08-24T09:15:00Z"
    assert event["location"] == "Room 4"
    assert event["description"] == "daily sync"
    assert event["html_link"] == "https://calendar.google.com/link"

    call_kwargs = service.events.return_value.list.call_args.kwargs
    assert call_kwargs["calendarId"] == "primary"
    assert call_kwargs["singleEvents"] is True


def test_list_events_multiple_calendar_ids(monkeypatch):
    service = _fake_service([_sample_item()])
    monkeypatch.setattr(gcal_events, "get_service", lambda: service)

    result = gcal_events.list_events(
        "2026-08-24T00:00:00Z", "2026-08-25T00:00:00Z",
        calendar_ids=["primary", "work@example.com"],
    )
    assert len(result) == 2
    assert service.events.return_value.list.call_count == 2


def test_list_events_handles_all_day_date_fields(monkeypatch):
    item = {
        "id": "evt-2",
        "summary": "Offsite",
        "start": {"date": "2026-08-30"},
        "end": {"date": "2026-08-31"},
    }
    monkeypatch.setattr(gcal_events, "get_service", lambda: _fake_service([item]))

    event = gcal_events.list_events("2026-08-30T00:00:00Z", "2026-08-31T00:00:00Z")[0]
    assert event["start_iso"] == "2026-08-30"
    assert event["end_iso"] == "2026-08-31"


# ---------------------------------------------------------------------------
# events.create_event payload
# ---------------------------------------------------------------------------


def test_create_event_payload_and_return(monkeypatch):
    service = MagicMock()
    created_raw = _sample_item()
    service.events.return_value.insert.return_value.execute.return_value = created_raw
    monkeypatch.setattr(gcal_events, "get_service", lambda: service)

    result = gcal_events.create_event(
        "Standup", "2026-08-24T09:00:00Z", "2026-08-24T09:15:00Z",
        description="daily sync", calendar_id="work@example.com",
    )

    kwargs = service.events.return_value.insert.call_args.kwargs
    assert kwargs["calendarId"] == "work@example.com"
    assert kwargs["body"] == {
        "summary": "Standup",
        "description": "daily sync",
        "start": {"dateTime": "2026-08-24T09:00:00Z"},
        "end": {"dateTime": "2026-08-24T09:15:00Z"},
    }
    assert result["title"] == "Standup"
    assert result["id"] == "evt-1"


# ---------------------------------------------------------------------------
# graph_sync against a real bi-temporal graph
# ---------------------------------------------------------------------------


def test_sync_to_graph_upserts_into_real_graph(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    start_a = now + timedelta(hours=5)
    start_b = now + timedelta(days=2)

    def fake_list(time_min, time_max, calendar_ids=None):
        return [
            {
                "id": "a",
                "title": "Dentist",
                "start_iso": start_a.isoformat(),
                "end_iso": (start_a + timedelta(hours=1)).isoformat(),
                "location": "Clinic",
                "description": "",
                "html_link": "https://link/a",
            },
            {
                "id": "b",
                "title": "Review",
                "start_iso": start_b.isoformat(),
                "end_iso": (start_b + timedelta(minutes=45)).isoformat(),
                "location": "",
                "description": "quarterly review",
                "html_link": "https://link/b",
            },
        ]

    monkeypatch.setattr(gcal_graph_sync, "list_events", fake_list)
    graph = BiTemporalEntityGraph(db_path=tmp_path / "graph.db")

    synced = gcal_graph_sync.sync_to_graph(graph=graph, days_ahead=7)

    assert sorted(synced) == ["[cal] Dentist", "[cal] Review"]

    entity = graph.get_active_entity("calendar_event", "[cal] Dentist")
    assert entity is None  # future event: valid_from > now, so not yet active

    history = graph.get_entity_history("calendar_event", "[cal] Dentist")
    assert len(history) == 1
    record = history[0]
    assert abs(record.valid_from - start_a.timestamp()) < 1.0
    assert record.properties["location"] == "Clinic"
    assert record.properties["html_link"] == "https://link/a"


def test_sync_supersedes_on_change(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=3)
    payloads = [
        {
            "id": "a",
            "title": "Sync Meeting",
            "start_iso": start.isoformat(),
            "end_iso": (start + timedelta(minutes=30)).isoformat(),
            "location": "Zoom",
            "description": "",
            "html_link": "https://link/v1",
        },
        {
            "id": "a",
            "title": "Sync Meeting",
            "start_iso": start.isoformat(),
            "end_iso": (start + timedelta(minutes=30)).isoformat(),
            "location": "Meet",
            "description": "",
            "html_link": "https://link/v2",
        },
    ]
    state = {"calls": 0}

    def fake_list(time_min, time_max, calendar_ids=None):
        idx = min(state["calls"], len(payloads) - 1)
        state["calls"] += 1
        return [payloads[idx]]

    monkeypatch.setattr(gcal_graph_sync, "list_events", fake_list)
    graph = BiTemporalEntityGraph(db_path=tmp_path / "graph.db")

    gcal_graph_sync.sync_to_graph(graph=graph)
    gcal_graph_sync.sync_to_graph(graph=graph)

    history = graph.get_entity_history("calendar_event", "[cal] Sync Meeting")
    assert len(history) == 2
    # Newest revision wins and carries updated properties.
    newest = max(history, key=lambda e: e.recorded_at)
    assert newest.properties["location"] == "Meet"


def test_events_between_overlapping_window(monkeypatch, tmp_path):
    base = datetime(2030, 6, 1, tzinfo=timezone.utc)

    def fake_list(time_min, time_max, calendar_ids=None):
        return [
            {
                "id": "x",
                "title": "Window Event",
                "start_iso": (base + timedelta(hours=10)).isoformat(),
                "end_iso": (base + timedelta(hours=12)).isoformat(),
                "location": "HQ",
                "description": "",
                "html_link": "",
            }
        ]

    monkeypatch.setattr(gcal_graph_sync, "list_events", fake_list)
    graph = BiTemporalEntityGraph(db_path=tmp_path / "graph.db")
    gcal_graph_sync.sync_to_graph(graph=graph, days_ahead=30)

    # Overlap inside the event window.
    hits = gcal_graph_sync.events_between(
        (base + timedelta(hours=11)).isoformat(),
        (base + timedelta(hours=13)).isoformat(),
        graph=graph,
    )
    assert len(hits) == 1
    assert hits[0]["name"] == "[cal] Window Event"
    assert hits[0]["properties"]["location"] == "HQ"

    # No overlap after the event ends.
    misses = gcal_graph_sync.events_between(
        (base + timedelta(hours=13)).isoformat(),
        (base + timedelta(hours=14)).isoformat(),
        graph=graph,
    )
    assert misses == []


# ---------------------------------------------------------------------------
# auth fallbacks
# ---------------------------------------------------------------------------


def test_is_connected_false_without_keyring(monkeypatch):
    monkeypatch.setattr(gcal_auth, "_keyring_get", lambda: None)
    monkeypatch.setattr(gcal_auth, "_read_env_fallback", lambda: "")
    assert gcal_auth.is_connected() is False


def test_get_service_raises_documented_error_when_not_connected(monkeypatch):
    monkeypatch.setattr(gcal_auth, "_keyring_get", lambda: None)
    monkeypatch.setattr(gcal_auth, "_read_env_fallback", lambda: "")
    if not gcal_auth.GOOGLE_LIBS_AVAILABLE:
        with pytest.raises(ImportError):
            gcal_auth.get_service()
    else:
        with pytest.raises(RuntimeError, match="not connected"):
            gcal_auth.get_service()


def test_get_service_raises_when_client_config_missing(monkeypatch):
    monkeypatch.setattr(gcal_auth, "_keyring_get", lambda: "refresh-token")
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_ID", raising=False)
    if not gcal_auth.GOOGLE_LIBS_AVAILABLE:
        pytest.skip("google libraries not installed")
    with pytest.raises(RuntimeError, match="client configuration"):
        gcal_auth.get_service()


def test_load_client_config_from_secret_file(monkeypatch, tmp_path):
    secret_file = tmp_path / "client_secret.json"
    secret_file.write_text(
        json.dumps({"installed": {"client_id": "abc", "client_secret": "xyz"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_SECRET", str(secret_file))
    cfg = gcal_auth.load_client_config()
    assert cfg["installed"]["client_id"] == "abc"


def test_load_client_config_from_raw_values(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_ID", "my-id")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_SECRET", "my-secret-value")
    cfg = gcal_auth.load_client_config()
    assert cfg["installed"]["client_id"] == "my-id"
    assert cfg["installed"]["client_secret"] == "my-secret-value"


def test_load_client_config_none_when_unset(monkeypatch):
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_ID", raising=False)
    assert gcal_auth.load_client_config() is None


def test_env_fallback_roundtrip_and_disconnect(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(gcal_auth, "_env_file_path", lambda: env_file)

    assert gcal_auth._write_env_fallback("tok-123") is True
    assert f"{gcal_auth._ENV_FALLBACK_KEY}=tok-123" in env_file.read_text(encoding="utf-8")

    monkeypatch.setattr(gcal_auth, "_keyring_get", lambda: None)
    assert gcal_auth.resolve_refresh_token() == "tok-123"
    assert gcal_auth.is_connected() is True

    assert gcal_auth.disconnect() is True
    assert gcal_auth._ENV_FALLBACK_KEY not in env_file.read_text(encoding="utf-8")
    assert gcal_auth.resolve_refresh_token() == ""
    assert gcal_auth.is_connected() is False


def test_persist_prefers_keyring_but_keeps_fallback(monkeypatch, tmp_path):
    stored: dict[str, str] = {}
    monkeypatch.setattr(gcal_auth, "_keyring_set", lambda tok: stored.setdefault("t", tok) is not None)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(gcal_auth, "_env_file_path", lambda: env_file)

    gcal_auth._persist_refresh_token("dual-tok")
    assert stored.get("t") == "dual-tok"
    assert f"{gcal_auth._ENV_FALLBACK_KEY}=dual-tok" in env_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# config schema defaults
# ---------------------------------------------------------------------------


def test_google_calendar_config_defaults():
    settings = GoogleCalendarSettings()
    assert settings.enabled is False
    assert settings.calendar_ids == ["primary"]
    assert settings.sync_interval_minutes == 60

    config = AJAConfig()
    assert config.google_calendar is None


def test_google_calendar_config_mounts_on_ajaconfig():
    config = AJAConfig.model_validate(
        {
            "google_calendar": {
                "enabled": True,
                "calendar_ids": ["primary", "work@example.com"],
                "sync_interval_minutes": 15,
            }
        }
    )
    assert config.google_calendar is not None
    assert config.google_calendar.enabled is True
    assert config.google_calendar.calendar_ids == ["primary", "work@example.com"]
    assert config.google_calendar.sync_interval_minutes == 15
