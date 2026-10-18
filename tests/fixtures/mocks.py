"""
tests/fixtures/mocks.py - Centralized canonical mocks and fakes for AJA test suites.

Eliminates duplicate mock definitions across tests while ensuring deterministic,
reproducible test runs across Windows, Linux, and macOS.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Sequence, Union


class BaseMockGateway:
    """Canonical mock LLM gateway supporting FIFO responses, error injection,

    call telemetry tracking, and streaming simulation.
    """

    def __init__(
        self,
        responses: Optional[Sequence[Any]] = None,
        default_response: Any = "Finished.",
        failure_models: Optional[Dict[str, Exception]] = None,
        provider: str = "mock",
        success_response: Any = None,
    ) -> None:
        self.responses: List[Any] = list(responses) if responses is not None else []
        self.default_response = success_response if success_response is not None else default_response
        self.failure_models: Dict[str, Exception] = failure_models or {}
        self.provider = provider

        # Call telemetry
        self.calls: List[Dict[str, Any]] = []
        self.models_called: List[str] = []
        self.system_prompts_seen: List[Optional[str]] = []
        self.call_count: int = 0
        self.turn: int = 0
        self.last_prompt: Any = None
        self.last_system_prompt: Optional[str] = None
        self.last_tools: Any = None

    @property
    def history_models(self) -> List[str]:
        return self.models_called

    async def chat(
        self,
        model: Optional[str] = None,
        prompt: Any = None,
        system: Optional[str] = None,
        tools: Any = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Simulate LLM chat completion."""
        self.call_count += 1
        self.turn += 1
        if model is not None:
            self.models_called.append(model)
        self.last_prompt = prompt
        self.last_system_prompt = system
        self.last_tools = tools
        self.system_prompts_seen.append(system)

        call_record = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "tools": tools,
            "kwargs": kwargs,
            "turn": self.turn,
        }
        self.calls.append(call_record)

        # Failure model error injection
        if model and model in self.failure_models:
            exc = self.failure_models[model]
            raise exc

        # Pop from FIFO queue if available
        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                resp = resp(model=model, prompt=prompt, system=system, tools=tools)
                if asyncio.iscoroutine(resp):
                    resp = await resp
            return resp

        # Queue exhausted -> default response
        if callable(self.default_response):
            val = self.default_response(model=model, prompt=prompt, system=system, tools=tools)
            if asyncio.iscoroutine(val):
                val = await val
            return val
        return self.default_response

    async def stream_chat(
        self,
        model: Optional[str] = None,
        prompt: Any = None,
        system: Optional[str] = None,
        tools: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Simulate streaming response tokens."""
        result = await self.chat(model=model, prompt=prompt, system=system, tools=tools, stream=True, **kwargs)
        if isinstance(result, dict) and "content" in result:
            text = result["content"] or ""
        elif isinstance(result, str):
            text = result
        else:
            text = str(result)

        # Yield in chunks
        chunk_size = max(1, len(text) // 3)
        for i in range(0, len(text), chunk_size):
            yield {"delta": {"content": text[i : i + chunk_size]}}


class BaseMockRegistry:
    """Canonical mock tool registry returning tool schemas for agent planning."""

    def __init__(self, schemas: Optional[Sequence[Dict[str, Any]]] = None) -> None:
        self.schemas: List[Dict[str, Any]] = list(schemas) if schemas is not None else []
        self.queries_seen: List[Dict[str, Any]] = []

    def get_schemas(self, interactive: bool = True, profile: Any = None) -> List[Dict[str, Any]]:
        self.queries_seen.append({"interactive": interactive, "profile": profile})
        return list(self.schemas)

    def register(self, schema: Dict[str, Any]) -> None:
        self.schemas.append(schema)


class BaseMockExecutor:
    """Canonical mock tool and shell command executor."""

    def __init__(
        self,
        default_stdout: str = "done",
        command_handler: Optional[Callable[[str, Optional[str]], Dict[str, Any]]] = None,
    ) -> None:
        self.default_stdout = default_stdout
        self.command_handler = command_handler
        self.dispatched_tool_calls: List[Dict[str, Any]] = []
        self.executed_commands: List[Dict[str, Any]] = []

    async def dispatch_tool_calls(
        self,
        tool_calls: Sequence[Dict[str, Any]],
        trace_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> List[Dict[str, Any]]:
        for tc in tool_calls:
            self.dispatched_tool_calls.append({"tool_call": tc, "trace_id": trace_id, "dry_run": dry_run})
        return []

    async def execute_async(
        self,
        cmd: str,
        cwd: Optional[str] = None,
        timeout: float = 60.0,
    ) -> Dict[str, Any]:
        self.executed_commands.append({"cmd": cmd, "cwd": cwd, "timeout": timeout})
        if self.command_handler:
            return self.command_handler(cmd, cwd)
        return {
            "status": "success",
            "stdout": self.default_stdout,
            "stderr": "",
            "exit_code": 0,
        }

    def execute(
        self,
        cmd: str,
        cwd: Optional[str] = None,
        timeout: float = 60.0,
    ) -> Dict[str, Any]:
        self.executed_commands.append({"cmd": cmd, "cwd": cwd, "timeout": timeout})
        if self.command_handler:
            return self.command_handler(cmd, cwd)
        return {
            "status": "success",
            "stdout": self.default_stdout,
            "stderr": "",
            "exit_code": 0,
        }


class BaseFakeBot:
    """In-memory mock bot for Telegram/Discord integration tests."""

    def __init__(self) -> None:
        self.sent_messages: List[Dict[str, Any]] = []
        self.actions_sent: List[Dict[str, Any]] = []
        self.current_message_id = 100

    async def send_message(
        self,
        chat_id: Union[int, str],
        text: str,
        parse_mode: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
        reply_markup: Any = None,
    ) -> Dict[str, Any]:
        self.current_message_id += 1
        record = {
            "message_id": self.current_message_id,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_to_message_id": reply_to_message_id,
            "reply_markup": reply_markup,
        }
        self.sent_messages.append(record)
        return record

    async def send_chat_action(self, chat_id: Union[int, str], action: str) -> bool:
        self.actions_sent.append({"chat_id": chat_id, "action": action})
        return True


class BaseFakeMemory:
    """In-memory vector store and key-value state for memory testing."""

    def __init__(self) -> None:
        self.kv_store: Dict[str, Any] = {}
        self.vectors: List[Dict[str, Any]] = []

    async def get(self, key: str, default: Any = None) -> Any:
        return self.kv_store.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        self.kv_store[key] = value

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        # Simple substring matching fake
        matches = [v for v in self.vectors if query.lower() in str(v.get("content", "")).lower()]
        return matches[:limit]

    async def add_vector(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        doc_id = f"doc_{len(self.vectors) + 1}"
        self.vectors.append({"id": doc_id, "content": content, "metadata": metadata or {}})
        return doc_id
