import json
import agentx_native

def test_translation():
    request = {
        "model": "claude-3-5-sonnet",
        "instructions": [{"type": "text", "text": "You are a helpful assistant."}],
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "text", "text": "What is the weather today?"}]
            }
        ],
        "generation": {"max_output_tokens": 1024}
    }
    
    request_json = json.dumps(request)
    translated_json = agentx_native.translate_to_anthropic(request_json)
    translated = json.loads(translated_json)
    
    print("--- Translated Request ---")
    print(json.dumps(translated, indent=2))
    
    # Assertions
    assert translated["model"] == "claude-3-5-sonnet"
    assert translated["system"] == "You are a helpful assistant."
    assert translated["messages"][0]["role"] == "user"
    assert translated["messages"][0]["content"][0]["text"] == "What is the weather today?"
    print("\n✅ Translation Test Passed!")

if __name__ == "__main__":
    test_translation()
