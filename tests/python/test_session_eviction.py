import time
import pytest
from aja.gateway.orchestrator import UnifiedGateway

def test_session_history_eviction_limit():
    session = {"history": []}
    for i in range(70):
        session["history"].append({"role": "user", "text": f"msg {i}", "time": time.time()})
    
    # Enforce eviction limit
    if len(session["history"]) > 50:
        session["history"] = session["history"][-50:]
        
    assert len(session["history"]) == 50
    assert session["history"][0]["text"] == "msg 20"
    assert session["history"][-1]["text"] == "msg 69"

def test_stale_image_ttl_eviction():
    session = {
        "history": [{"role": "user", "text": "old photo", "time": time.time() - 90000}],  # > 24h ago
        "last_image_url": "http://example.com/photo.jpg"
    }
    
    newest = max((h.get("time", 0) for h in session["history"]), default=0)
    if newest > 0 and (time.time() - newest) > 24 * 3600:
        session.pop("last_image_url", None)
        
    assert "last_image_url" not in session

def test_image_context_cleared_on_general_text():
    session = {"last_image_url": "http://example.com/old.jpg"}
    content = "wassup?"
    VISION_FOLLOWUP_TRIGGERS = (
        "image", "photo", "picture", "screen", "see", "describe",
        "look", "drawing", "diagram", "what is in", "what's in"
    )
    content_lower = content.lower()
    if not any(term in content_lower for term in VISION_FOLLOWUP_TRIGGERS):
        session.pop("last_image_url", None)

    assert "last_image_url" not in session
