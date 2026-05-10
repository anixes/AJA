import requests
import json
import time
import subprocess
import re

def get_vram_usage():
    try:
        output = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,nounits,noheader"], encoding='utf-8')
        return int(output.strip())
    except:
        return 0

def measure_tps(context_size):
    url = "http://localhost:8080/completion"
    
    # Accurate token approximation: 1 token is ~4 characters in English
    # We build a string of context_size * 4 characters
    prompt_context = "AgentX context test block. " * (context_size // 5)
    prompt = f"{prompt_context}\n\nTask: Verify the integrity of the above context and state your current TPS metric."

    payload = {
        "prompt": prompt,
        "n_predict": 50,
        "stream": True,
        "temperature": 0.0,
        "cache_prompt": True
    }

    start_time = time.time()
    tokens = 0
    first_token_time = None

    try:
        response = requests.post(url, json=payload, stream=True, timeout=300)
        for line in response.iter_lines():
            if line:
                decoded = line.decode('utf-8')
                if decoded.startswith("data: "):
                    data = json.loads(decoded[6:])
                    if not first_token_time:
                        first_token_time = time.time()
                    tokens += 1
                    if data.get("stop"):
                        break
        
        end_time = time.time()
        # Prompt Processing Time (PP) - Initial Latency
        pp_time = first_token_time - start_time
        # Token Generation Time (TG)
        tg_time = end_time - first_token_time
        tps = tokens / tg_time if tg_time > 0 else 0
        
        vram_used = get_vram_usage()
        
        return {
            "context_size": context_size,
            "latency_ttft": pp_time,
            "tps": tps,
            "vram_mb": vram_used,
            "tokens": tokens
        }
    except Exception as e:
        return {"error": str(e)}

def run_suite():
    # Context sizes to test (incremental)
    # We stay below the 128k limit to avoid context shifting performance hits
    sizes = [1024, 8192, 16384, 32768, 64000, 96000]
    results = []
    
    print(f"--- Context Performance & VRAM Report ---")
    print(f"{'Size':<10} | {'Latency':<10} | {'TPS':<10} | {'VRAM (MB)':<10}")
    print("-" * 50)

    for size in sizes:
        res = measure_tps(size)
        if "error" in res:
            print(f"{size:<10} | ERROR: {res['error']}")
        else:
            print(f"{size:<10} | {res['latency_ttft']:>8.2f}s | {res['tps']:>8.2f} | {res['vram_mb']:>8}")
            results.append(res)
        # Give the GPU a tiny break
        time.sleep(2)

    with open("context_test_results.json", "w") as f:
        json.dump(results, f, indent=4)

if __name__ == "__main__":
    run_suite()
