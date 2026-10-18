import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from rich.panel import Panel

from aja.mcp.catalog import get_catalog, check_dependencies, install_mcp_server
from aja.tui.curses_tui import TerminalDashboard

@pytest.fixture(autouse=True)
def mock_config_paths(tmp_path, monkeypatch):
    import aja.config
    # Create mock directories
    project_dir = tmp_path / "project"
    data_dir = tmp_path / "data"
    project_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    monkeypatch.setattr(aja.config, "PROJECT_ROOT", project_dir)
    monkeypatch.setattr(aja.config, "DATA_DIR", data_dir)
    
    # Also patch catalog's references to them
    import aja.mcp.catalog
    monkeypatch.setattr(aja.mcp.catalog, "PROJECT_ROOT", project_dir)
    monkeypatch.setattr(aja.mcp.catalog, "DATA_DIR", data_dir)

def test_get_catalog():
    catalog = get_catalog()
    assert isinstance(catalog, dict)
    assert "sqlite" in catalog
    assert "postgres" in catalog
    assert "github" in catalog
    assert "command" in catalog["sqlite"]
    assert "args" in catalog["sqlite"]

def test_check_dependencies():
    with patch("shutil.which") as mock_which:
        # Mock node available
        mock_which.side_effect = lambda cmd: "/usr/bin/node" if cmd == "node" else None
        assert check_dependencies("npx") is True
        assert check_dependencies("node") is True
        assert check_dependencies("python") is False

        # Mock python available
        mock_which.side_effect = lambda cmd: "/usr/bin/python" if cmd == "python" else None
        assert check_dependencies("pip") is True
        assert check_dependencies("python") is True
        assert check_dependencies("node") is False

        # Mock other random binary
        mock_which.side_effect = lambda cmd: "/usr/bin/git" if cmd == "git" else None
        assert check_dependencies("git") is True
        assert check_dependencies("npx") is False

def test_install_mcp_server_validation():
    # Test invalid server
    with pytest.raises(ValueError, match="not found in catalog"):
        install_mcp_server("non_existent_server")

    # Test missing dependency
    with patch("aja.mcp.catalog.check_dependencies", return_value=False):
        with pytest.raises(RuntimeError, match="Missing dependency"):
            install_mcp_server("sqlite")

def test_install_mcp_server_success(tmp_path):
    import aja.config
    project_config = aja.config.PROJECT_ROOT / "aja.json"
    data_config = aja.config.DATA_DIR / "aja.json"

    # Pre-populate project config with some data
    initial_config = {
        "project_name": "AJA",
        "mcp_servers": []
    }
    with project_config.open("w", encoding="utf-8") as f:
        json.dump(initial_config, f, indent=4)

    with patch("aja.mcp.catalog.check_dependencies", return_value=True):
        # Install sqlite server
        res = install_mcp_server("sqlite")
        assert res is True

        # Verify project aja.json
        assert project_config.exists()
        with project_config.open("r", encoding="utf-8") as f:
            data = json.load(f)
        
        assert "mcp_servers" in data
        assert isinstance(data["mcp_servers"], list)
        assert len(data["mcp_servers"]) == 1
        server_entry = data["mcp_servers"][0]
        assert server_entry["server_id"] == "sqlite"
        assert server_entry["transport"] == "stdio"
        assert server_entry["enabled"] is True
        assert server_entry["command"] == "npx"
        assert "@modelcontextprotocol/server-sqlite" in server_entry["args"]

        # Verify data aja.json sync
        assert data_config.exists()
        with data_config.open("r", encoding="utf-8") as f:
            data_sync = json.load(f)
        assert data_sync == data

def test_install_mcp_server_update_existing():
    import aja.config
    project_config = aja.config.PROJECT_ROOT / "aja.json"

    # Pre-populate project config with existing enabled=False server
    initial_config = {
        "mcp_servers": [
            {
                "server_id": "sqlite",
                "transport": "stdio",
                "enabled": False,
                "command": "old_command",
                "args": []
            }
        ]
    }
    with project_config.open("w", encoding="utf-8") as f:
        json.dump(initial_config, f, indent=4)

    with patch("aja.mcp.catalog.check_dependencies", return_value=True):
        install_mcp_server("sqlite")

        with project_config.open("r", encoding="utf-8") as f:
            data = json.load(f)
        
        assert len(data["mcp_servers"]) == 1
        server_entry = data["mcp_servers"][0]
        assert server_entry["server_id"] == "sqlite"
        assert server_entry["enabled"] is True
        assert server_entry["command"] == "npx"
        assert len(server_entry["args"]) > 0

def test_terminal_dashboard_mcp_hub_navigation():
    # Instantiate dashboard
    dashboard = TerminalDashboard(dry_run=True)
    assert dashboard.selected_mcp_index == 0
    assert len(dashboard.mcp_catalog) > 0

    # Test keypress down / s
    num_items = len(dashboard.mcp_catalog)
    dashboard.handle_keypress("down")
    assert dashboard.selected_mcp_index == 1

    dashboard.handle_keypress("s")
    assert dashboard.selected_mcp_index == 2

    # Test keypress t toggles skin theme
    old_skin = dashboard.current_skin_key
    dashboard.handle_keypress("t")
    assert dashboard.current_skin_key != old_skin

    # Go beyond limit
    for _ in range(num_items + 2):
        dashboard.handle_keypress("down")
    assert dashboard.selected_mcp_index == num_items - 1

    # Test keypress up / w
    dashboard.handle_keypress("up")
    assert dashboard.selected_mcp_index == num_items - 2

    dashboard.handle_keypress("w")
    assert dashboard.selected_mcp_index == num_items - 3

    # Go beyond upper limit
    for _ in range(num_items + 2):
        dashboard.handle_keypress("up")
    assert dashboard.selected_mcp_index == 0

def test_terminal_dashboard_mcp_hub_refresh():
    dashboard = TerminalDashboard(dry_run=True)
    
    # Mock get_catalog
    new_catalog = {"custom_mcp": {"description": "Custom description", "command": "custom", "args": []}}
    with patch("aja.tui.curses_tui.get_catalog", return_value=new_catalog):
        dashboard.handle_keypress("r")
        assert "custom_mcp" in dashboard.mcp_catalog
        assert len(dashboard.mcp_catalog) == 1

def test_terminal_dashboard_mcp_hub_install():
    dashboard = TerminalDashboard(dry_run=True)
    dashboard.selected_mcp_index = 0
    catalog_items = list(dashboard.mcp_catalog.items())
    server_name = catalog_items[0][0]

    with patch("aja.tui.curses_tui.install_mcp_server") as mock_install, \
         patch("aja.config.load_and_validate_config") as mock_load_config:
        
        dashboard.handle_keypress("i")
        mock_install.assert_called_once_with(server_name)
        mock_load_config.assert_called_once()
        
        # Check logs contain success message
        success_logs = [log for log in dashboard.logs if "SUCCESS" in log and server_name in log]
        assert len(success_logs) > 0

def test_terminal_dashboard_mcp_hub_panel_rendering():
    dashboard = TerminalDashboard(dry_run=True)
    panel = dashboard.render_mcp_hub_panel()
    assert isinstance(panel, Panel)
    assert "MCP Hub" in str(panel.title)
