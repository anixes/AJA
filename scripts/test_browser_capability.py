import sys
import os

# Add the packages directory to sys.path to allow imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'packages', 'agentx-core')))

from agentx.capabilities import registry

def test_browser():
    print("--- Testing browser.search ---")
    try:
        search_cap = registry.get("browser.search")
        res = search_cap.execute({"query": "AI News May 2026"})
        
        if res.success:
            print(f"Success! Found content of length: {len(res.output.get('content', ''))}")
            print(f"Snippet: {res.output.get('content', '')[:200]}...")
        else:
            print(f"Failed: {res.error}")
    except Exception as e:
        print(f"Error during test: {e}")

if __name__ == "__main__":
    test_browser()
