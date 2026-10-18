import sys
import json
import pytest
from unittest.mock import MagicMock, patch
from aja.main import main
import aja.main

def test_cli_brief(capsys, monkeypatch):
    """Test that --brief prints the brief file contents and exits with 0."""
    monkeypatch.setattr(sys, "argv", ["main.py", "--brief"])
    
    with pytest.raises(SystemExit) as excinfo:
        main()
        
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "AJA" in captured.out or "Orchestration" in captured.out

def test_cli_agent_doctor(capsys, monkeypatch):
    """Test that doctor command with --agent flag produces correct JSON output."""
    monkeypatch.setattr(sys, "argv", ["main.py", "doctor", "--agent"])
    
    # Mock diagnostics to avoid actual network/system checks
    mock_checks = [
        ("Config Validation", True, "ok"),
        ("Native Engine", True, "ok"),
    ]
    with patch("aja.utils.diagnostics.run_diagnostics", return_value=mock_checks):
        main()
            
    captured = capsys.readouterr()
    # Check if stdout contains valid JSON
    data = json.loads(captured.out.strip())
    assert data["status"] == "ok"
    assert len(data["checks"]) == 2
    assert data["checks"][0]["name"] == "Config Validation"
    assert data["checks"][0]["passed"] is True

def test_cli_agent_status(capsys, monkeypatch):
    """Test that status command with --agent flag produces correct JSON output."""
    monkeypatch.setattr(sys, "argv", ["main.py", "status", "--agent"])
    
    # Mock data to avoid database hits and disk access
    with patch("aja.memory.manager.get_memory_manager"), \
         patch("aja.persistence.tasks.fetch_pending_tasks", return_value=[]), \
         patch("pathlib.Path.exists", return_value=False):
        main()
        
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert "mode" in data
    assert "batons" in data
    assert "tasks" in data

def test_cli_agent_help(capsys, monkeypatch):
    """Test that help command with --agent flag produces JSON schema of capabilities."""
    monkeypatch.setattr(sys, "argv", ["main.py", "help", "--agent"])
    
    main()
        
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert "help" in data
    assert "commands" in data
    assert "rules" in data
    assert "skills" in data

def test_cli_mode_flags(monkeypatch):
    """Test that --agent and --human flags set AGENT_MODE appropriately."""
    # Test --agent explicitly
    monkeypatch.setattr(sys, "argv", ["main.py", "status", "--agent"])
    with patch("aja.main.cmd_status") as mock_status:
        main()
        assert aja.main.AGENT_MODE is True
        
    # Test --human explicitly
    monkeypatch.setattr(sys, "argv", ["main.py", "status", "--human"])
    with patch("aja.main.cmd_status") as mock_status:
        main()
        assert aja.main.AGENT_MODE is False

def test_cli_smart_defaulting(monkeypatch):
    """Test smart defaulting based on TTY status when no mode flags are supplied."""
    monkeypatch.setattr(sys, "argv", ["main.py", "status"])
    
    # Non-TTY should default to AGENT_MODE = True
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    with patch("aja.main.cmd_status") as mock_status:
        main()
        assert aja.main.AGENT_MODE is True
        
    # TTY should default to AGENT_MODE = False
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    with patch("aja.main.cmd_status") as mock_status:
        main()
        assert aja.main.AGENT_MODE is False
