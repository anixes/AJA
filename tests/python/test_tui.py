import pytest
from agentx.tui.curses_tui import TerminalDashboard, SKINS

def test_tui_initialization_and_skins():
    db = TerminalDashboard(dry_run=True)
    assert db.dry_run is True
    assert db.current_skin_key == "cyberpunk"
    
    # Toggle skin
    db.toggle_skin()
    assert db.current_skin_key == "ares"
    
    db.toggle_skin()
    assert db.current_skin_key == "default"
    
    db.toggle_skin()
    assert db.current_skin_key == "cyberpunk"
    
    skin = db.get_skin()
    assert skin["name"] == "Cyberpunk Neon Grid"

def test_tui_layout_generation():
    db = TerminalDashboard(dry_run=True)
    
    # Generate layout and assert there are no exceptions
    layout = db.generate_layout()
    assert layout is not None
    
    # Verify viewports exist in split layout
    assert layout.get("top") is not None
    assert layout.get("bottom") is not None
    assert layout.get("left") is not None
    assert layout.get("right") is not None


def test_tui_simulated_activities():
    db = TerminalDashboard(dry_run=True)
    
    # Verify initial state has active running nodes
    running_nodes = [n for n in db.nodes if n["status"] == "RUNNING"]
    assert len(running_nodes) > 0
    
    # Simulate activity ticks to trigger node transitions
    for _ in range(20):
        db.generate_simulated_activity()
        
    # Verify that new logs are appended to tail
    assert len(db.logs) > 0
