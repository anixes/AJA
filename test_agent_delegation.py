import requests
import json

def test_delegation():
    prompt = """
    GOAL: Create a robust Python FastAPI system for a Local Library. 
    You are the LEAD AGENT. You have 3 sub-agents available: 'Architect', 'Coder', and 'QualityControl'.
    
    TASK: 
    1. Break down the project into 3 specific sub-tasks.
    2. Delegate each sub-task to the correct agent.
    3. Output the delegation plan in STRICT JSON format.
    
    JSON FORMAT:
    {
      "plan_name": "Library API",
      "assignments": [
        {"agent": "AgentName", "instruction": "Specific detailed task"}
      ]
    }
    """
    
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.0
    }
    
    print("Testing Delegation...")
    response = requests.post("http://localhost:8080/v1/chat/completions", json=payload)
    
    try:
        content = response.json()['choices'][0]['message']['content']
        # Extract JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        
        parsed = json.loads(content)
        print("SUCCESS! Delegation Plan Created:")
        print(json.dumps(parsed, indent=2))
        
        # Verify if it assigned all 3 agents correctly
        agents = [a['agent'] for a in parsed['assignments']]
        if all(x in agents for x in ['Architect', 'Coder', 'QualityControl']):
            print("\nVERDICT: Excellent Leadership. All specialized agents assigned.")
        else:
            print("\nVERDICT: Weak Leadership. Some agents were skipped.")
            
    except Exception as e:
        print(f"FAILED! Error: {e}")
        print(f"Raw Output: {response.text}")

if __name__ == "__main__":
    test_delegation()
