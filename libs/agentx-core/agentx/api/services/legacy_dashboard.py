"""Legacy dashboard launcher compatibility helpers."""

from agentx.config import PROJECT_ROOT


def dashboard_unavailable_payload() -> dict:
    dashboard_path = PROJECT_ROOT / "apps" / "dashboard"
    return {
        "ok": False,
        "status": "deprecated",
        "message": "Dashboard web UI is deprecated; use the local CLI/TUI surfaces.",
        "path": str(dashboard_path),
    }
