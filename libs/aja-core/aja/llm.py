import json
import logging
import os
import asyncio
import threading
from concurrent.futures import Future
from typing import List, Dict, Any, Optional

import aja.config
from aja.orchestration.gateway import LLMGateway
from aja.api.interfaces import BaseModelProvider

logger = logging.getLogger(__name__)

# Gateway instance cache: cache_key -> LLMGateway
_gateway_cache: Dict[str, LLMGateway] = {}
# BaseModelProvider gateway cache (bounded): cache_key -> LLMGateway
_provider_gateway_cache: Dict[str, LLMGateway] = {}
_PROVIDER_GATEWAY_CACHE_MAX = 16
# Default gateway singleton (lazily initialized by get_gateway())
_gateway = None


def clear_gateway_cache():
    """Clear cached gateway instances (e.g. after config or token changes)."""
    global _gateway_cache, _gateway
    _gateway_cache.clear()
    _provider_gateway_cache.clear()
    _gateway = None


def _get_cached_provider_gateway(provider, api_key, base_url=None, extra_headers=None):
    """Return a shared LLMGateway for a BaseModelProvider call.

    Reusing one gateway per (provider, key, base_url) avoids the previous
    per-call construction that leaked an unclosed gateway on every request.
    """
    import hashlib

    key_fragment = (
        hashlib.sha256((api_key or "").encode()).hexdigest()[:12] if api_key else ""
    )
    cache_key = f"{provider}:{key_fragment}:{base_url or ''}"
    gw = _provider_gateway_cache.get(cache_key)
    if gw is None:
        if len(_provider_gateway_cache) >= _PROVIDER_GATEWAY_CACHE_MAX:
            _provider_gateway_cache.pop(next(iter(_provider_gateway_cache)))
        kwargs = {"extra_headers": extra_headers} if extra_headers else {}
        gw = LLMGateway(
            provider=provider, api_key=api_key or "", base_url=base_url, **kwargs
        )
        _provider_gateway_cache[cache_key] = gw
    return gw


def _choices_from_chat_result(res):
    """Shape a gateway.chat() result into an OpenAI-style choices dict.

    ``res`` is a plain string when no tools were requested; when tools were
    forwarded and the provider surfaced tool calls, it is a dict with
    ``content``/``tool_calls``.
    """
    if isinstance(res, dict):
        message = {"role": "assistant", "content": res.get("content") or ""}
        tool_calls = res.get("tool_calls") or []
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", ""),
                    },
                }
                for tc in tool_calls
                if isinstance(tc, dict)
            ]
        return {"choices": [{"message": message}]}
    if not res:
        # Failure sentinel (None) or empty completion — log it instead of
        # silently shaping the outage into a legitimate-looking empty message.
        logger.warning("[LLM] gateway.chat returned no content for provider call")
    return {"choices": [{"message": {"role": "assistant", "content": res or ""}}]}


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

def resolve_provider_model(
    model_str,
    operating_mode,
    local_model_fallback,
    cloud_model_fallback,
    capability: Optional[str] = None,
):
    """
    Pure model-routing resolution: 'provider:model' parsing + operating-mode
    override + capability-based auto-routing. Returns (provider, model_name).

    Modes:
      local / offline   — cloud providers redirect to the local fallback
      cloud / online    — local llama_cpp attempts redirect to the cloud fallback
      hybrid            — capability-driven auto-router:
                          if capability == 'vision' and active model lacks vision,
                          dynamically auto-routes to active local/cloud vision engine.
      swarm             — direct role assignment
    """
    provider = "openrouter"  # Default
    if not model_str:
        model_str = "google:gemini-2.5-flash"
    model_name = model_str

    if ":" in model_str:
        parts = model_str.split(":", 1)
        provider = parts[0].strip().lower()
        model_name = parts[1].strip()
    else:
        # Smart detection fallback
        low = model_str.lower()
        if "gemini" in low:
            provider = "google"
        elif "ollama" in low:
            provider = "ollama"
        elif any(k in low for k in ["gemma", "llama", "qwen", "mistral", "lfm", "showui"]) or low.endswith(".gguf"):
            provider = "llama_cpp"
        elif "copilot" in low:
            provider = "copilot"
            if model_name.lower() in ("copilot", ""):
                model_name = "gpt-4o"

    mode_clean = (operating_mode or "hybrid").lower().strip()

    # 1. Local / Offline Override
    if mode_clean in ("offline", "local"):
        if provider in ["google", "openai", "anthropic", "openrouter", "copilot"]:
            logger.info("[LLM] LOCAL MODE: Redirecting %s:%s -> llama_cpp:%s", provider, model_name, local_model_fallback)
            provider = "llama_cpp"
            model_name = local_model_fallback

    # 2. Cloud / Online Override
    elif mode_clean in ("online", "cloud"):
        if provider in ["llama_cpp", "ollama", "lm_studio"]:
            logger.info("[LLM] CLOUD MODE: Redirecting %s:%s -> google:%s", provider, model_name, cloud_model_fallback)
            provider = "google"
            model_name = cloud_model_fallback

    # 3. Hybrid Mode (Capability Auto-Routing)
    elif mode_clean == "hybrid":
        if capability == "vision":
            from aja.models.model_spec import infer_capabilities, ModelCapability
            caps = infer_capabilities(model_name, provider=provider)
            if ModelCapability.VISION not in caps:
                try:
                    from aja.models.local_manager import LocalModelManager
                    vis_model = LocalModelManager.get_active_vision_model()
                    if vis_model:
                        logger.info("[LLM] HYBRID MODE: Auto-routing vision request to '%s'", vis_model)
                        if ":" in vis_model:
                            provider, model_name = vis_model.split(":", 1)
                            provider = provider.lower().strip()
                        else:
                            provider, model_name = "llama_cpp", vis_model
                except Exception as e:
                    logger.debug("LocalModelManager vision auto-route skipped: %s", e)

    return provider, model_name


def get_gateway_for_model(model_str, capability: Optional[str] = None):
    """
    Returns a gateway instance configured for the specific model and capability.
    Supports 'provider:model_name' syntax.
    """
    # 1. Check Operating Mode from aja.json
    operating_mode = "hybrid"
    local_model_fallback = "gemma-4-e2b"
    cloud_model_fallback = "gemini-2.5-flash"
    try:
        config_path = os.path.join(aja.config.PROJECT_ROOT, "aja.json")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                swarm_cfg = cfg.get("swarm_settings", {})
                operating_mode = swarm_cfg.get("operating_mode")
                if not operating_mode:
                    offline_mode = swarm_cfg.get("offline_mode", False)
                    operating_mode = "local" if offline_mode else "cloud"

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

    provider, model_name = resolve_provider_model(
        model_str, operating_mode, local_model_fallback, cloud_model_fallback, capability=capability
    )

    # Get API key from environment
    api_key = os.getenv(f"{provider.upper()}_API_KEY", "")
    if not api_key and provider == "google":
        api_key = os.getenv("GEMINI_API_KEY", "")

    # Hash the key instead of truncating: a truncated prefix can collide
    # between distinct keys and never refreshes after key rotation.
    import hashlib
    key_fragment = hashlib.sha256(api_key.encode()).hexdigest()[:12] if api_key else ""
    cache_key = f"{provider}:{key_fragment}"
    if cache_key not in _gateway_cache:
        _gateway_cache[cache_key] = LLMGateway(provider=provider, api_key=api_key)

    return _gateway_cache[cache_key], model_name

def run_async_synchronously(coro):
    """
    Runs a coroutine synchronously, handling both cases where an event loop
    is already running in the current thread or not.

    Hardened: BaseException-safe, never deadlocks if the worker thread dies
    before resolving the future, and never masks the original error with an
    UnboundLocalError from closing a loop that was never created.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    res_future = Future()

    def thread_target():
        worker_loop = None
        try:
            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            try:
                result = worker_loop.run_until_complete(coro)
            except BaseException as exc:
                res_future.set_exception(exc)
            else:
                res_future.set_result(result)
        except BaseException as exc:
            # Loop creation/setup itself failed; coro was never awaited.
            res_future.set_exception(exc)
            coro.close()
        finally:
            if worker_loop is not None:
                try:
                    worker_loop.close()
                except Exception:
                    pass

    t = threading.Thread(target=thread_target, daemon=True)
    t.start()
    t.join()
    if not res_future.done():
        raise RuntimeError(
            "run_async_synchronously worker thread terminated without producing a result"
        )
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

        gw = _get_cached_provider_gateway("google", api_key, base_url)
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
            temperature=temperature,
            tools=tools,
        ))
        if not res:
            logger.warning(
                "[LLM] Google generate-content returned no content (model=%s)", model
            )
        return _choices_from_chat_result(res or "")

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

        gw = _get_cached_provider_gateway("openai", api_key, base_url)
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
            tools=tools
        ))
        return _choices_from_chat_result(res)

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

        gw = _get_cached_provider_gateway("openrouter", api_key, base_url)
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
            tools=tools
        ))
        return _choices_from_chat_result(res)

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

        gw = _get_cached_provider_gateway("llama_cpp", api_key, base_url)
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
            tools=tools
        ))
        return _choices_from_chat_result(res)

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
            logger.info("[Copilot] No valid GitHub token found (or token lacks Copilot scopes). Initiating device code login...")
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

        gw = _get_cached_provider_gateway(
            "copilot", api_token, base_url, extra_headers=headers
        )

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
            tools=tools,
            extra_body=extra_body if extra_body else None
        ))
        return _choices_from_chat_result(res)

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
                logger.warning("[LLM] Failed to load dynamic provider %s: %s", ep.name, e)
    except Exception:
        pass

discover_providers()


# --- Core completion API ---

def completion(prompt, system_prompt="You are a helpful assistant.", model=None, temperature=None, tools=None) -> Optional[Any]:
    """
    Standard completion interface used across AJA.
    Enforces operating_mode (local/cloud/hybrid/swarm) from aja.json.
    Auto-detects vision capabilities and routes accordingly.

    Returns:
        str or dict (when tools requested) on success; ``None`` on failure/empty.
    """
    # Detect capability requirements
    capability = None
    if isinstance(prompt, list):
        for m in prompt:
            c = m.get("content")
            if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
                capability = "vision"
                break

    if model is None:
        try:
            from aja.models.local_manager import LocalModelManager
            active_info = LocalModelManager.get_active_model()
            model = active_info.get("active_model") or "llama_cpp:LFM2.5-VL-1.6B"
        except Exception:
            model = "llama_cpp:LFM2.5-VL-1.6B"

    # Enforce operating mode override (local/cloud/hybrid) and capability routing
    gw, model_name = get_gateway_for_model(model, capability=capability)
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
            res = provider_inst.chat_completions(messages, tools=tools)
            choices = res.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content")
                tool_calls = msg.get("tool_calls", [])
                if tools is not None:
                    return {"content": content or "", "tool_calls": tool_calls}
                if not content:
                    logger.warning(
                        "[LLM] Registered provider '%s' returned no content", provider
                    )
                return content or None
            logger.warning(
                "[LLM] Registered provider '%s' returned no choices", provider
            )
            return None
        except Exception as e:
            logger.error("[LLM] Error using registered provider '%s': %s. Falling back to LLMGateway.", provider, e)

    gw_res = run_async_synchronously(gw.chat(model=model_name, prompt=prompt, system=system_prompt, temperature=temperature, tools=tools))
    if tools is not None and isinstance(gw_res, str):
        return {"content": gw_res, "tool_calls": []}
    return gw_res


async def completion_async(prompt, system_prompt="You are a helpful assistant.", model=None, temperature=None, tools=None) -> Optional[Any]:
    """
    Native async completion interface without OS thread-switching overhead.

    Returns:
        str or dict on success; ``None`` on provider failure/empty output.
    """
    capability = None
    if isinstance(prompt, list):
        for m in prompt:
            c = m.get("content")
            if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
                capability = "vision"
                break

    if model is None:
        try:
            from aja.models.local_manager import LocalModelManager
            active_info = LocalModelManager.get_active_model()
            model = active_info.get("active_model") or "llama_cpp:LFM2.5-VL-1.6B"
        except Exception:
            model = "llama_cpp:LFM2.5-VL-1.6B"

    gw, model_name = get_gateway_for_model(model, capability=capability)
    return await gw.chat(model=model_name, prompt=prompt, system=system_prompt, temperature=temperature, tools=tools)


async def completion_stream(prompt, system_prompt="You are a helpful assistant.", model=None, temperature=None):
    """
    Stream token chunks directly from the configured LLM gateway.
    """
    capability = None
    if isinstance(prompt, list):
        for m in prompt:
            c = m.get("content")
            if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
                capability = "vision"
                break

    if model is None:
        try:
            from aja.models.local_manager import LocalModelManager
            active_info = LocalModelManager.get_active_model()
            model = active_info.get("active_model") or "llama_cpp:LFM2.5-VL-1.6B"
        except Exception:
            model = "llama_cpp:LFM2.5-VL-1.6B"

    gw, model_name = get_gateway_for_model(model, capability=capability)
    async for chunk in gw.chat_stream(model=model_name, prompt=prompt, system=system_prompt, temperature=temperature):
        yield chunk


