"""Async-safe helpers for legacy websocket broadcast surfaces."""

import asyncio
import json
from typing import Any, Awaitable, Callable


def make_serializable(obj: Any) -> Any:
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {key: make_serializable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [make_serializable(value) for value in obj]
    return obj


def event_message(event_type: str, payload: Any) -> str:
    return json.dumps(
        {
            "type": "event_broadcast",
            "event_type": event_type,
            "data": make_serializable(payload),
        }
    )


def dispatch_broadcast(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Run a broadcast coroutine from sync or async event-bus handlers."""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.create_task(coro_factory())
            return
    except RuntimeError:
        pass

    try:
        asyncio.run(coro_factory())
    except Exception:
        pass
