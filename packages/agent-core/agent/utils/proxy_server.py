from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx
import uvicorn

app = FastAPI(title="Unified BYO-API Proxy")

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024

import json
from pathlib import Path

def load_providers():
    try:
        path = Path("providers.json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"openai": "https://api.openai.com/v1"}

PROVIDERS = load_providers()

def get_provider_url(model: str, custom_url: Optional[str]) -> str:
    """Claude Logic: Auto-detect provider or use override."""
    if custom_url:
        return custom_url.rstrip("/")
    
    if model.startswith("nvidia/"):
        return PROVIDERS.get("nvidia", "https://integrate.api.nvidia.com/v1")
    if "versatile" in model or model.startswith("llama3") or model.startswith("mixtral"):
        return PROVIDERS.get("groq", "https://api.groq.com/openai/v1")
    if "together" in model:
        return PROVIDERS.get("together", "https://api.together.xyz/v1")
    
    return PROVIDERS.get("openai", "https://api.openai.com/v1")

@app.post("/v1/chat/completions")
async def proxy_chat(
    request: ChatRequest,
    authorization: str = Header(None),
    x_provider_url: str = Header(None, alias="X-Provider-Url")
):
    """
    Enhanced Proxy with Claude-inspired Fail-Open and Auto-Routing.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    target_base = get_provider_url(request.model, x_provider_url)
    target_url = f"{target_base}/chat/completions"
    
    print(f"[Gateway] Routing {request.model} to {target_base}")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                target_url,
                headers={"Authorization": authorization, "Content-Type": "application/json"},
                json=request.dict(),
                timeout=60.0
            )
            
            # Claude Logic: If provider fails, don't crash, return structured error
            if response.status_code != 200:
                return {
                    "error": {
                        "message": f"Provider {target_base} returned {response.status_code}",
                        "details": response.text
                    }
                }
                
            return response.json()
        except Exception as e:
            # Claude Logic: Fail-open / Graceful warning
            print(f"[Gateway] CRITICAL: {str(e)}")
            return {
                "error": {
                    "message": "Gateway Fail-Open triggered.",
                    "reason": str(e)
                }
            }

if __name__ == "__main__":
    print("Unified Gateway Proxy starting on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
