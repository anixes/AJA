import time
import sys
import os

# Add the libs directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "libs/aja-core")))

from aja.interface.intent_parser import local_router_fallback

human_cases = [
    ("Polite request", "hey can you list the files in the data science folder inside the d drive please?"),
    ("Need to see", "i need to see what's inside D:\\data science"),
    ("Could you show", "could you show me the files in that data science directory?"),
    ("What files are in", "what files are in the data science folder?"),
    ("Quick check", "run a quick doctor check"),
    ("Clear screen", "just clear the screen"),
    ("GPU check", "can you check my gpu usage for me?"),
    ("Debug error", "hey why did that last command fail?"),
    ("Find files", "can you find me the python files in this project?"),
    ("Simple read", "read that config file for me"),
    ("Logs check", "show me the recent logs"),
    ("Typo command", "lsi files in data science"),
]

print(f"{'Use Case':<25} | {'Query':<65} | {'Result':<15} | {'Time (ms)':<10}")
print("-" * 125)

total_local = 0
total_llm = 0

for name, query in human_cases:
    start_time = time.perf_counter()
    result = local_router_fallback(query)
    end_time = time.perf_counter()
    
    latency_ms = (end_time - start_time) * 1000
    
    if result is not None:
        result_str = f"LOCAL ({result.get('command', result.get('type', ''))})"
        total_local += 1
    else:
        result_str = "LLM Fallback"
        total_llm += 1
        
    print(f"{name:<25} | {query:<65} | {result_str:<15} | {latency_ms:.3f} ms")

print("-" * 125)
print(f"Summary: {total_local} routed locally, {total_llm} sent to LLM.")
