import requests

def test_state_tracking():
    # 1. Give the secret
    # 2. Add distracting content
    # 3. Ask for secret
    distractions = [
        "What is 15 * 24?",
        "Write a 4-line poem about coffee.",
        "List 5 countries in Africa.",
        "Who wrote the play Hamlet?",
        "Translate 'Good Morning' to Spanish."
    ]
    
    messages = [{"role": "system", "content": "You are a state-tracking agent. The secret vault key is BLUE_PANDA. Do not forget it."}]
    
    for d in distractions:
        messages.append({"role": "user", "content": d})
        # We don't even need the assistant's replies for this test, we just fill the context.
        messages.append({"role": "assistant", "content": "Processed."})
        
    messages.append({"role": "user", "content": "Emergency! I need the secret vault key now. What is it?"})
    
    payload = {
        "messages": messages,
        "stream": False,
        "temperature": 0.0
    }
    
    print("Testing Memory...")
    response = requests.post("http://localhost:8080/v1/chat/completions", json=payload)
    print(f"Model Response: {response.json()['choices'][0]['message']['content']}")

if __name__ == "__main__":
    test_state_tracking()
