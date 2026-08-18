import json
import os
import asyncio
import threading
from concurrent.futures import Future
from typing import List, Dict, Any, Optional

import aja.config
from aja.orchestration.gateway import LLMGateway
from aja.api.interfaces import BaseModelProvider

# Gateway instance cache: cache_key -> LLMGateway
_gateway_cache: Dict[str, LLMGateway] = {}


def clear_gateway_cache():
    """Clear cached gateway instances (e.g. after config or token changes)."""
    global _gateway_cache, _gateway
    _gateway_cache.clear()
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
                worker_model = models_cfg.get("worker", "")
                if worker_model:
                    if ":" in worker_model:
                        prov, m_name = worker_model.split(":", 1)
                        if prov not in ["google", "openai", "anthropic", "openrouter", "copilot"]:
                            local_model_fallback = m_name
                    elif worker_model not in ["google", "openai", "anthropic", "openrouter", "copilot"]:
                        local_model_fallback = worker_model
                        
                planner_model = models_cfg.get("planner", "")
                if planner_model:
                    if ":" in planner_model:
                        prov, m_name = planner_model.split(":", 1)
                        if prov in ["google", "openai", "anthropic", "openrouter", "copilot"]:
                            cloud_model_fallback = m_name
                    elif planner_model in ["google", "openai", "anthropic", "openrouter", "copilot"]:
                        pass
                    else:
                        cloud_model_fallback = planner_model
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
        elif "ollama" in model_str.lower():
            provider = "ollama"
        elif "gemma" in model_str.lower() or "llama" in model_str.lower() or "qwen" in model_str.lower() or "mistral" in model_str.lower():
            provider = "llama_cpp"
        elif "copilot" in model_str.lower():
            provider = "copilot"
            if model_name.lower() in ("copilot", ""):
                model_name = "gpt-4o"

    # 2. Apply Operating Mode Override
    if operating_mode == "offline" and provider in ["google", "openai", "anthropic", "openrouter", "copilot"]:
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

    cache_key = f"{provider}:{api_key[:8] if api_key else ''}"
    if cache_key not in _gateway_cache:
        _gateway_cache[cache_key] = LLMGateway(provider=provider, api_key=api_key)

    return _gateway_cache[cache_key], model_name

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
        
        # Test if the token actually has Copilot scopes by exchanging it
        api_token = None
        if raw_token:
            try:
                from aja.copilot_auth import exchange_copilot_token
                api_token, _ = exchange_copilot_token(raw_token)
            except Exception:
                api_token = raw_token
                
        if not raw_token or not api_token:
            print("\n[Copilot] No valid GitHub token found (or token lacks Copilot scopes). Initiating device code login...")
            raw_token = copilot_device_code_login()
            if not raw_token:
                raise ValueError("Copilot authentication failed. Please provide a valid GitHub token.")
            try:
                from aja.copilot_auth import exchange_copilot_token
                api_token, _ = exchange_copilot_token(raw_token)
            except Exception:
                api_token = raw_token
            
        # Detect if any image input is present to flag is_vision
        is_vision = False
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        is_vision = True
                        break
            if is_vision:
                break
                
        headers = copilot_request_headers(
            is_agent_turn=self.config.get("is_agent_turn", True),
            is_vision=is_vision
        )
        
        base_url = self.config.get("base_url") or "https://api.githubcopilot.com"
        model = self.config.get("model", "gpt-4o-mini")
        if model in ("copilot", "github-copilot", "default"):
            model = "gpt-4o-mini"
            
        # Build extra body configuration (e.g. reasoning effort)
        extra_body = {}
        reasoning_config = self.config.get("reasoning_config") or self.config.get("reasoning")
        if reasoning_config and isinstance(reasoning_config, dict):
            effort = reasoning_config.get("effort")
            if effort:
                extra_body["reasoning"] = {"effort": effort}
                
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
            temperature=temperature,
            extra_body=extra_body if extra_body else None
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

def completion(prompt, system_prompt="You are a helpful assistant.", model=None, temperature=None, tools=None):
    """
    Standard completion interface used across AJA.
    Enforces operating_mode (online/offline/hybrid) from aja.json.
    """
    if model is None:
        try:
            config_path = os.path.join(aja.config.PROJECT_ROOT, "aja.json")
            with open(config_path, "r") as f:
                config = json.load(f)
                model = config.get("swarm_settings", {}).get("models", {}).get("planner", "llama_cpp:LFM2.5-VL-1.6B")
        except Exception:
            model = "llama_cpp:LFM2.5-VL-1.6B"

    # Enforce operating mode override (offline/online/hybrid)
    gw, model_name = get_gateway_for_model(model)
    provider = gw.provider

    # Try dynamic provider registry first
    provider_cls = provider_registry.get(provider)
    if provider_cls:
        api_key = os.getenv(f"{provider.upper()}_API_KEY", "")
        if not api_key and provider == "google":
            api_key = os.getenv("GEMINI_API_KEY", "")

        provider_inst = provider_cls({
            "model": model_name,
            "provider": provider,
            "api_key": api_key,
            "temperature": temperature
        })

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

    # Gateway execution path
    return run_async_synchronously(gw.chat(model=model_name, prompt=prompt, system=system_prompt, temperature=temperature, tools=tools)) or ""


async def completion_async(prompt, system_prompt="You are a helpful assistant.", model=None, temperature=None, tools=None):
    """
    Native async completion interface without OS thread-switching overhead.
    """
    if model is None:
        try:
            config_path = os.path.join(aja.config.PROJECT_ROOT, "aja.json")
            with open(config_path, "r") as f:
                config = json.load(f)
                model = config.get("swarm_settings", {}).get("models", {}).get("planner", "llama_cpp:LFM2.5-VL-1.6B")
        except Exception:
            model = "llama_cpp:LFM2.5-VL-1.6B"

    gw, model_name = get_gateway_for_model(model)
    return await gw.chat(model=model_name, prompt=prompt, system=system_prompt, temperature=temperature, tools=tools) or ""


async def completion_stream(prompt, system_prompt="You are a helpful assistant.", model=None, temperature=None):
    """
    Stream token chunks directly from the configured LLM gateway.
    """
    if model is None:
        try:
            config_path = os.path.join(aja.config.PROJECT_ROOT, "aja.json")
            with open(config_path, "r") as f:
                config = json.load(f)
                model = config.get("swarm_settings", {}).get("models", {}).get("planner", "llama_cpp:LFM2.5-VL-1.6B")
        except Exception:
            model = "llama_cpp:LFM2.5-VL-1.6B"

    gw, model_name = get_gateway_for_model(model)
    async for chunk in gw.chat_stream(model=model_name, prompt=prompt, system=system_prompt, temperature=temperature):
        yield chunk


