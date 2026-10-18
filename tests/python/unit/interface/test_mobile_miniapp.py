import json
import pytest
from fastapi.testclient import TestClient

from aja.api.bridge import app
from aja.orchestration.tools.mobile import (
    mobile_send_sms,
    mobile_get_battery,
    mobile_get_location,
    mobile_push_notification,
    mobile_manager,
)
from aja.orchestration.tools.native import NativeToolRegistry


@pytest.fixture
def client():
    return TestClient(app)


def test_miniapp_static_mount(client):
    """Verify that Telegram Mini App static files are mounted at /app."""
    response = client.get("/app/")
    assert response.status_code == 200
    assert "<title>AJA Mission Control</title>" in response.text
    assert "telegram-web-app.js" in response.text


def test_miniapp_static_assets(client):
    """Verify CSS and JS assets are served properly."""
    css_res = client.get("/app/styles.css")
    assert css_res.status_code == 200
    assert "--terminal-bg" in css_res.text

    js_res = client.get("/app/app.js")
    assert js_res.status_code == 200
    assert "AJA Mission Control" in js_res.text


def test_mobile_tools_execution():
    """Verify mobile tools execution and return formatting."""
    battery_raw = mobile_get_battery()
    battery = json.loads(battery_raw)
    assert "level_percent" in battery
    assert battery["level_percent"] > 0

    loc_raw = mobile_get_location()
    loc = json.loads(loc_raw)
    assert "latitude" in loc
    assert "geofence" in loc

    sms_res = mobile_send_sms(to="+15551234567", message="Hello from AJA")
    assert "queued for delivery" in sms_res.lower()

    notify_res = mobile_push_notification(title="Test Alert", body="Mission complete")
    assert "delivered" in notify_res.lower()


def test_native_tool_registry_mobile_schemas():
    """Verify NativeToolRegistry registers and exposes mobile tools."""
    registry = NativeToolRegistry()
    assert "mobile_send_sms" in registry.tools
    assert "mobile_get_battery" in registry.tools
    assert "mobile_get_location" in registry.tools
    assert "mobile_push_notification" in registry.tools

    schemas = registry.get_schemas()
    schema_names = [s["function"]["name"] for s in schemas]
    assert "mobile_send_sms" in schema_names
    assert "mobile_get_battery" in schema_names
    assert "mobile_get_location" in schema_names
    assert "mobile_push_notification" in schema_names
