import requests
import json
import time

def stress_test():
    url = "http://localhost:8080/completion"
    prompt = """Plan a complex project to build a local agent swarm called 'AgentX'. 
The swarm must run on a machine with only 4GB VRAM. 
1. Break the project into 5 technical phases.
2. For each phase, identify the primary model role (Planner vs Worker).
3. Specify the exact model and quantization (e.g. Gemma-4-E2B Q4_K_M) that fits in the 4GB limit.
4. Explain how you will handle context window exhaustion during a long coding task.
5. Provide a sample 'thought block' that the swarm would generate when encountering a CUDA OOM error."""

    payload = {
        "prompt": prompt,
        "n_predict": 1024,
        "stream": True,
        "temperature": 0.0,
        "reasoning": True
    }

    print(f"--- Starting Agentic Stress Test ---\n")
    start_time = time.time()
    tokens_received = 0
    first_token_time = None

    try:
        response = requests.post(url, json=payload, stream=True)
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data: "):
                    data = json.loads(decoded_line[6:])
                    if not first_token_time:
                        first_token_time = time.time()
                    
                    content = data.get("content", "")
                    print(content, end="", flush=True)
                    tokens_received += 1
                    
                    if data.get("stop"):
                        break
        
        end_time = time.time()
        duration = end_time - start_time
        tps = tokens_received / duration if duration > 0 else 0
        ttft = (first_token_time - start_time) if first_token_time else 0

        print(f"\n\n--- Test Complete ---")
        print(f"Total Tokens: {tokens_received}")
        print(f"Duration: {duration:.2f}s")
        print(f"Average TPS: {tps:.2f}")
        print(f"TTFT: {ttft:.2f}s")

    except Exception as e:
        print(f"\nError during stress test: {e}")

if __name__ == "__main__":
    stress_test()
