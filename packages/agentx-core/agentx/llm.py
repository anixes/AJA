
import json
import os
from agentx.orchestration.gateway import UnifiedGateway

# Singleton gateway instance
_gateway = None

def get_gateway():
    global _gateway
    if _gateway is None:
        _gateway = UnifiedGateway()
    return _gateway

def get_gateway_for_model(model_str):
    """
    Returns a gateway instance configured for the specific model.
    Supports 'provider:model_name' syntax.
    """
    # 1. Check Operating Mode from agentx.json
    operating_mode = "online"
    local_model_fallback = "gemma-4-e2b"
    cloud_model_fallback = "gemini-2.5-flash"
    try:
        if os.path.exists("agentx.json"):
            with open("agentx.json", "r") as f:
                cfg = json.load(f)
                operating_mode = cfg.get("swarm_settings", {}).get("operating_mode")
                if not operating_mode:
                    offline_mode = cfg.get("swarm_settings", {}).get("offline_mode", False)
                    operating_mode = "offline" if offline_mode else "online"

                # Allow overriding the local fallback model
                local_model_fallback = cfg.get("swarm_settings", {}).get("models", {}).get("worker", local_model_fallback)
                if ":" in local_model_fallback:
                    local_model_fallback = local_model_fallback.split(":")[1]
                    
                cloud_model_fallback = cfg.get("swarm_settings", {}).get("models", {}).get("planner", cloud_model_fallback)
                if ":" in cloud_model_fallback:
                    cloud_model_fallback = cloud_model_fallback.split(":")[1]
    except Exception:
        pass

    provider = "openrouter" # Default
    model_name = model_str

    if ":" in model_str:
        parts = model_str.split(":", 1)
        provider = parts[0]
        model_name = parts[1]
    else:
        # Smart detection fallback
        if "gemini" in model_str.lower():
            provider = "google"
        elif "gemma" in model_str.lower() or "llama" in model_str.lower():
            provider = "llama_cpp"

    # 2. Apply Operating Mode Override
    if operating_mode == "offline" and provider in ["google", "openai", "anthropic", "openrouter"]:
        print(f"[LLM] OFFLINE MODE ACTIVE: Redirecting {provider}:{model_name} -> llama_cpp:{local_model_fallback}")
        provider = "llama_cpp"
        model_name = local_model_fallback
    elif operating_mode == "hybrid":
        # In hybrid mode, both local and cloud are allowed.
        pass
    elif operating_mode == "online" and provider == "llama_cpp":
        print(f"[LLM] ONLINE MODE ACTIVE: Redirecting {provider}:{model_name} -> google:{cloud_model_fallback}")
        provider = "google"
        model_name = cloud_model_fallback

    # Get API key from environment
    api_key = os.getenv(f"{provider.upper()}_API_KEY", "")
    if not api_key and provider == "google":
        api_key = os.getenv("GEMINI_API_KEY", "")

    return UnifiedGateway(provider=provider, api_key=api_key), model_name

def completion(prompt, system_prompt="You are a helpful assistant.", model=None):
    """
    Standard completion interface used across Agent.
    Routes to the correct provider based on model name/prefix.
    """
    if model is None:
        try:
            with open("agentx.json", "r") as f:
                config = json.load(f)
                model = config.get("swarm_settings", {}).get("models", {}).get("planner", "google:gemini-2.5-flash")
        except Exception:
            model = "google:gemini-2.5-flash"
            
    gw, model_name = get_gateway_for_model(model)
    return gw.chat(model=model_name, prompt=prompt, system=system_prompt) or ""
