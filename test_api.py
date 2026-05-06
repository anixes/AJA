import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.append(str(PROJECT_ROOT))

load_dotenv()

from scripts.core.gateway import UnifiedGateway

def test_gateway():
    provider = "google"
    key = os.getenv("GOOGLE_API_KEY")
    model = "gemini-flash-latest"
    
    print(f"Testing Provider: {provider}")
    print(f"Key present: {bool(key)}")
    print(f"Model: {model}")
    
    try:
        gateway = UnifiedGateway(provider, key)
        print(f"Gateway initialized with provider: {gateway.provider}")
        print(f"Base URL: {gateway.base_url}")
        
        response = gateway.chat(model, "Say 'Hello from AgentX Test'")
        print(f"Response: {response}")
    except Exception as e:
        print(f"Error during test: {e}")

if __name__ == "__main__":
    test_gateway()
