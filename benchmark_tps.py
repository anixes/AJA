import requests
import time
import json

def test_inference(test_name):
    prompt = """
    Below is a search report from May 2026 regarding breakthroughs in Room-Temperature Superconductors.
    Summarize the key findings and explain why the five-criterion validation standard is so important for the scientific community.
    
    REPORT:
    As of May 2026, there is no confirmed breakthrough achieving a stable, ambient-pressure room-temperature superconductor. 
    The search for this "holy grail" remains an active and highly rigorous area of materials science.
    
    Key Findings:
    - Five-criterion validation standard: Zero resistance above 273K, Meissner effect, Specific heat anomaly, Independent replication (3+ groups), Stability at ambient pressure.
    - High-Pressure Superhydrides: LaH10 has validated critical temperature of ~260K, but only under millions of atmospheres.
    - Argonne/Northwestern (May 2026): Moving toward theory-driven material design rather than Edisonian trial-and-error.
    - City University of Hong Kong (April 2026): Identified "re-entrant superconductivity" in nickelates.
    - Physical Review Letters (April 2026): Direct imaging of particle pairing in Fermi gas.
    - Mercury-based ceramic Hg-1223: Reached 151K via "pressure quenching" in March 2026.
    """

    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are a scientific expert analyzing the latest breakthroughs."},
            {"role": "user", "content": prompt}
        ],
        "stream": True
    }

    print(f"\n--- RUNNING {test_name} ---")
    start_time = time.time()
    response = requests.post("http://localhost:8080/v1/chat/completions", json=payload, stream=True)
    
    first_token_time = None
    token_count = 0

    for line in response.iter_lines():
        if line:
            if first_token_time is None:
                first_token_time = time.time()
            
            decoded_line = line.decode('utf-8')
            if decoded_line.startswith("data: "):
                try:
                    data = json.loads(decoded_line[6:])
                    if "choices" in data and len(data["choices"]) > 0:
                        content = data["choices"][0]["delta"].get("content", "")
                        if content:
                            token_count += 1
                except:
                    pass

    end_time = time.time()
    ttft = first_token_time - start_time
    total_time = end_time - first_token_time
    tps = token_count / total_time if total_time > 0 else 0

    print(f"Time to First Token (TTFT): {ttft:.4f}s")
    print(f"Tokens Per Second (TPS): {tps:.2f}")

if __name__ == "__main__":
    test_inference("FIRST RUN (Cold Cache)")
    time.sleep(2)
    test_inference("SECOND RUN (Warm Cache - Prompt Caching Test)")
