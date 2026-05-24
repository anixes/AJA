import time
import requests
import json

def test_model(model_name, prompt):
    print(f"\n[TEST] Testing Model: {model_name}...")
    url = "http://localhost:8080/completion"
    payload = {
        "prompt": prompt,
        "n_predict": 128,
        "stream": False
    }
    
    start_time = time.time()
    try:
        response = requests.post(url, json=payload, timeout=120)
        end_time = time.time()
        
        if response.status_code == 200:
            data = response.json()
            duration = end_time - start_time
            # llama.cpp server response has content and timings
            # Note: The response format might vary depending on the version
            # Usually it returns text in "content"
            content = data.get("content", "")
            tokens_generated = len(content.split()) # Rough estimate if not provided
            
            # Try to get exact token count from timings if available
            timings = data.get("timings", {})
            predicted_n = timings.get("predicted_n", 0)
            if predicted_n > 0:
                tokens_generated = predicted_n
            
            tps = tokens_generated / duration if duration > 0 else 0
            
            print(f"  - Status   : Success")
            print(f"  - Duration : {duration:.2f} seconds")
            print(f"  - Tokens   : {tokens_generated}")
            print(f"  - Speed    : {tps:.2f} tokens/sec")
            return tps
        else:
            print(f"  - Status   : Failed (Code {response.status_code})")
            print(f"  - Error    : {response.text}")
            return 0
    except Exception as e:
        print(f"  - Status   : Error ({str(e)})")
        return 0

if __name__ == "__main__":
    test_prompt = "Write a Python script to sort a list of numbers without using .sort()."
    
    # We are testing the current running llama-server
    models = ["gemma-4-e2b-it-local"]
    results = {}
    
    print("--- AJA Model Performance Benchmark (llama-server) ---")
    for m in models:
        tps = test_model(m, test_prompt)
        results[m] = tps
        
    print("\n+-------------------------+-----------------+")
    print("| Model Name              | Performance     |")
    print("+-------------------------+-----------------+")
    for m, tps in results.items():
        print(f"| {m:<23} | {tps:>8.2f} TPS |")
    print("+-------------------------+-----------------+")
    print("Note: Goal is >20 TPS for fluid agentic swarms.")
