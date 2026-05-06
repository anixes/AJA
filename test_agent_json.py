import requests
import json

def test_json_strictness():
    prompt = 'Output a list of 3 users in STRICT JSON format. Each user must have a "profile" object with "name", "bio", and an array of "skills". The "bio" MUST contain a double quote character like this: "Hello". Output ONLY the JSON.'
    
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.0
    }
    
    print("Sending Request...")
    response = requests.post("http://localhost:8080/v1/chat/completions", json=payload)
    
    try:
        content = response.json()['choices'][0]['message']['content']
        # Extract JSON if there's markdown
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        
        parsed = json.loads(content)
        print("SUCCESS! JSON is valid.")
        print(json.dumps(parsed, indent=2))
    except Exception as e:
        print("FAILED!")
        print(f"Error: {e}")
        print(f"Raw Content: {response.text}")

if __name__ == "__main__":
    test_json_strictness()
