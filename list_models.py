import requests
import os
from dotenv import load_dotenv

load_dotenv()

def list_models():
    key = os.getenv("GOOGLE_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            models = response.json().get('models', [])
            for m in models:
                print(f"Name: {m['name']}, Display: {m['displayName']}")
        else:
            print(f"Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    list_models()
