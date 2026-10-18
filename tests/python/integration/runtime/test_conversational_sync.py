import pytest
from aja.memory.secretary import AJAMemory, get_aja_memory

def test_conversational_sync_mirroring():
    """
    Verify that conversational turns can be mirrored to LanceDB in real-time
    and retrieved correctly from the chat_history table.
    """
    memory = get_aja_memory()
    
    # Mirror a user message
    memory.mirror_chat_message(
        role="user",
        content="Hello, AJA! How is our swarm doing today?",
        metadata={"client": "test_suite"}
    )
    
    import time
    time.sleep(0.02)
    
    # Mirror an assistant message
    memory.mirror_chat_message(
        role="assistant",
        content="All systems are operating within nominal thresholds.",
        metadata={"model": "gemini-3-pro"}
    )
    
    # Retrieve and check the history
    history = memory.get_chat_history(limit=5)
    assert len(history) >= 2
    
    # The last two messages should match our insertions
    last_two = history[-2:]
    assert last_two[0]["role"] == "user"
    assert last_two[0]["content"] == "Hello, AJA! How is our swarm doing today?"
    assert last_two[0]["metadata"]["client"] == "test_suite"
    
    assert last_two[1]["role"] == "assistant"
    assert last_two[1]["content"] == "All systems are operating within nominal thresholds."
    assert last_two[1]["metadata"]["model"] == "gemini-3-pro"
