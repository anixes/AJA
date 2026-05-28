import json
import os
import asyncio
import threading
from concurrent.futures import Future
from typing import List, Dict, Any, Optional

import aja.config
from aja.orchestration.gateway import LLMGateway
from aja.api.interfaces import BaseModelProvider

# Singleton gateway instance
_gateway = None

def get_gateway():
    global _gateway
    if _gateway is None:
        model = "google:gemini-2.5-flash"
        try:
            config_path = os.path.join(aja.config.PROJECT_ROOT, "aja.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    config = json.load(f)
                    model = config.get("swarm_settings", {}).get("models", {}).get("planner", "google:gemini-2.5-flash")
        except Exception:
            pass
        _gateway, _ = get_gateway_for_model(model)
    return _gateway

def get_gateway_for_model(model_str):
    """
    Returns a gateway instance configured for the specific model.
    Supports 'provider:model_name' syntax.
    """
    # 1. Check Operating Mode from aja.json
    operating_mode = "online"
    local_model_fallback = "gemma-4-e2b"
    cloud_model_fallback = "gemini-2.5-flash"
    try:
        config_path = os.path.join(aja.config.PROJECT_ROOT, "aja.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                cfg = json.load(f)
                swarm_cfg = cfg.get("swarm_settings", {})
                operating_mode = swarm_cfg.get("operating_mode")
                if not operating_mode:
                    offline_mode = swarm_cfg.get("offline_mode", False)
                    operating_mode = "offline" if offline_mode else "online"

                # Allow overriding the fallback models
                models_cfg = swarm_cfg.get("models", {})
                local_model_fallback = models_cfg.get("worker", local_model_fallback)
                if ":" in local_model_fallback:
                    local_model_fallback = local_model_fallback.split(":")[1]
                    
                cloud_model_fallback = models_cfg.get("planner", cloud_model_fallback)
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

    return LLMGateway(provider=provider, api_key=api_key), model_name

def run_async_synchronously(coro):
    """
    Runs a coroutine synchronously, handling both cases where an event loop
    is already running in the current thread or not.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    res_future = Future()

    def thread_target():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coro)
            res_future.set_result(result)
        except Exception as e:
            res_future.set_exception(e)
        finally:
            loop.close()

    t = threading.Thread(target=thread_target)
    t.start()
    t.join()
    return res_future.result()


# --- Pluggable BaseModelProvider Classes ---

class GoogleModelProvider(BaseModelProvider):
    def chat_completions(
        self, 
        messages: List[Dict[str, str]], 
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        api_key = self.config.get("api_key") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("AI_KEY", "")
        base_url = self.config.get("base_url")
        model = self.config.get("model", "gemini-2.5-flash")
        temperature = self.config.get("temperature")
        
        gw = LLMGateway(provider="google", api_key=api_key, base_url=base_url)
        system = "You are a helpful assistant."
        contents = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", m.get("text", ""))
            else:
                contents.append(m)
        
        res = run_async_synchronously(gw._google_generate_content(
            model=model,
            prompt=contents,
            system=system,
            temperature=temperature
        ))
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": res
                    }
                }
            ]
        }

    def check_requirements(self) -> bool:
        api_key = self.config.get("api_key") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("AI_KEY", "")
        return bool(api_key)


class OpenAIModelProvider(BaseModelProvider):
    def chat_completions(
        self, 
        messages: List[Dict[str, str]], 
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        api_key = self.config.get("api_key") or os.getenv("OPENAI_API_KEY", "")
        base_url = self.config.get("base_url")
        model = self.config.get("model", "gpt-4")
        temperature = self.config.get("temperature")
        
        gw = LLMGateway(provider="openai", api_key=api_key, base_url=base_url)
        system = "You are a helpful assistant."
        contents = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", m.get("text", ""))
            else:
                contents.append(m)
        
        res = run_async_synchronously(gw.chat(
            model=model,
            prompt=contents,
            system=system,
            temperature=temperature
        ))
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": res
                    }
                }
            ]
        }

    def check_requirements(self) -> bool:
        api_key = self.config.get("api_key") or os.getenv("OPENAI_API_KEY", "")
        return bool(api_key)


class OpenRouterModelProvider(BaseModelProvider):
    def chat_completions(
        self, 
        messages: List[Dict[str, str]], 
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        api_key = self.config.get("api_key") or os.getenv("OPENROUTER_API_KEY", "")
        base_url = self.config.get("base_url")
        model = self.config.get("model", "google/gemini-2.5-flash")
        temperature = self.config.get("temperature")
        
        gw = LLMGateway(provider="openrouter", api_key=api_key, base_url=base_url)
        system = "You are a helpful assistant."
        contents = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", m.get("text", ""))
            else:
                contents.append(m)
        
        res = run_async_synchronously(gw.chat(
            model=model,
            prompt=contents,
            system=system,
            temperature=temperature
        ))
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": res
                    }
                }
            ]
        }

    def check_requirements(self) -> bool:
        api_key = self.config.get("api_key") or os.getenv("OPENROUTER_API_KEY", "")
        return bool(api_key)


class LlamaCppModelProvider(BaseModelProvider):
    def chat_completions(
        self, 
        messages: List[Dict[str, str]], 
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        api_key = self.config.get("api_key") or os.getenv("LLAMA_CPP_API_KEY", "no-key-needed")
        base_url = self.config.get("base_url")
        model = self.config.get("model", "gemma-4-e2b")
        temperature = self.config.get("temperature")
        
        gw = LLMGateway(provider="llama_cpp", api_key=api_key, base_url=base_url)
        system = "You are a helpful assistant."
        contents = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", m.get("text", ""))
            else:
                contents.append(m)
        
        res = run_async_synchronously(gw.chat(
            model=model,
            prompt=contents,
            system=system,
            temperature=temperature
        ))
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": res
                    }
                }
            ]
        }

    def check_requirements(self) -> bool:
        return True


class CopilotModelProvider(BaseModelProvider):
    def chat_completions(
        self, 
        messages: List[Dict[str, str]], 
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        from aja.copilot_auth import resolve_copilot_token, get_copilot_api_token, copilot_request_headers, copilot_device_code_login
        
        raw_token = self.config.get("api_key")
        if not raw_token:
            raw_token, _ = resolve_copilot_token()
        
        if not raw_token:
            print("[Copilot] No GitHub token found in environment. Initiating device code login...")
            raw_token = copilot_device_code_login()
            if not raw_token:
                raise ValueError("Copilot authentication failed. Please provide a valid GitHub token.")
                
        api_token = get_copilot_api_token(raw_token)
        headers = copilot_request_headers()
        
        base_url = self.config.get("base_url") or "https://api.githubcopilot.com"
        model = self.config.get("model", "gpt-4o")
        temperature = self.config.get("temperature")
        
        gw = LLMGateway(provider="copilot", api_key=api_token, base_url=base_url, extra_headers=headers)
        
        system = "You are a helpful assistant."
        contents = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", m.get("text", ""))
            else:
                contents.append(m)
        
        res = run_async_synchronously(gw.chat(
            model=model,
            prompt=contents,
            system=system,
            temperature=temperature
        ))
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": res
                    }
                }
            ]
        }

    def check_requirements(self) -> bool:
        return True



# --- Dynamic/Lazy Provider Registry ---

class ModelProviderRegistry:
    def __init__(self):
        self._providers = {}

    def register(self, name: str, cls):
        self._providers[name.lower()] = cls

    def get(self, name: str):
        return self._providers.get(name.lower())

    def list_providers(self):
        return list(self._providers.keys())

provider_registry = ModelProviderRegistry()

# Pre-register standard providers
provider_registry.register("google", GoogleModelProvider)
provider_registry.register("openai", OpenAIModelProvider)
provider_registry.register("openrouter", OpenRouterModelProvider)
provider_registry.register("llama_cpp", LlamaCppModelProvider)
provider_registry.register("copilot", CopilotModelProvider)

def discover_providers():
    """Discover and register extension model providers dynamically via entry_points."""
    try:
        import sys
        if sys.version_info >= (3, 10):
            from importlib.metadata import entry_points
            eps = entry_points(group="aja.model_providers")
        else:
            from importlib_metadata import entry_points
            eps = entry_points().get("aja.model_providers", [])
        for ep in eps:
            try:
                provider_cls = ep.load()
                provider_registry.register(ep.name, provider_cls)
            except Exception as e:
                print(f"[LLM] Failed to load dynamic provider {ep.name}: {e}")
    except Exception:
        pass

discover_providers()


# --- Core completion API ---

def completion(prompt, system_prompt="You are a helpful assistant.", model=None, temperature=None):
    """
    Standard completion interface used across AJA.
    Routes to the correct provider, preferring registered pluggable providers.
    """
    if model is None:
        try:
            config_path = os.path.join(aja.config.PROJECT_ROOT, "aja.json")
            with open(config_path, "r") as f:
                config = json.load(f)
                model = config.get("swarm_settings", {}).get("models", {}).get("planner", "google:gemini-2.5-flash")
        except Exception:
            model = "google:gemini-2.5-flash"
            
    # Resolve the model's provider and name
    provider = "openrouter"
    model_name = model
    if ":" in model:
        parts = model.split(":", 1)
        provider = parts[0]
        model_name = parts[1]
    else:
        if "gemini" in model.lower():
            provider = "google"
        elif "gemma" in model.lower() or "llama" in model.lower():
            provider = "llama_cpp"
            
    # Try dynamic provider registry first
    provider_cls = provider_registry.get(provider)
    if provider_cls:
        # Resolve api_key
        api_key = os.getenv(f"{provider.upper()}_API_KEY", "")
        if not api_key and provider == "google":
            api_key = os.getenv("GEMINI_API_KEY", "")
            
        provider_inst = provider_cls({
            "model": model_name,
            "provider": provider,
            "api_key": api_key,
            "temperature": temperature
        })
        
        # Build standard messages format
        messages = [{"role": "system", "content": system_prompt}]
        if isinstance(prompt, list):
            for m in prompt:
                messages.append({
                    "role": m.get("role", "user"),
                    "content": m.get("content", m.get("text", ""))
                })
        else:
            messages.append({"role": "user", "content": prompt})
            
        try:
            res = provider_inst.chat_completions(messages)
            choices = res.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "") or ""
            return ""
        except Exception as e:
            print(f"[LLM] Error using registered provider '{provider}': {e}. Falling back to LLMGateway.")
            
    # Legacy fallback
    gw, model_name = get_gateway_for_model(model)
    return run_async_synchronously(gw.chat(model=model_name, prompt=prompt, system=system_prompt, temperature=temperature)) or ""
