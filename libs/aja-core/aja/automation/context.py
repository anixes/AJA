"""
Automation workflow engine.

Users define workflows as Python classes with an async run(ctx) method.
The WorkflowContext provides tool execution, LLM calls, and delivery.
"""

from __future__ import annotations

import importlib.util
import logging
import time
from pathlib import Path
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)


class WorkflowContext:
    """Passed to each workflow step. Provides tool execution, LLM calls, and delivery."""

    def __init__(
        self,
        tools_registry: Any = None,
        gateway: Any = None,
        deliver_fn: Optional[Callable[[str], None]] = None,
        model: str = "",
        state: Optional[Dict[str, Any]] = None,
    ):
        self._tools = tools_registry
        self._gateway = gateway
        self._deliver_fn = deliver_fn
        self.model = model or "default"
        self.state = state if state is not None else {}

    async def tool(self, name: str, **kwargs: Any) -> str:
        """Executes a registered tool by name."""
        if self._tools is None:
            raise RuntimeError("No tools registry configured")
        result = self._tools.execute(name, kwargs)
        return result if isinstance(result, str) else str(result)

    async def llm(self, prompt: str, system: str = "You are a helpful assistant.") -> str:
        """Single LLM completion through the configured gateway."""
        if self._gateway is None:
            raise RuntimeError("No LLM gateway configured")
        return await self._gateway.chat(model=self.model, prompt=prompt, system=system)

    async def deliver(self, text: str) -> None:
        """Publishes result on the event bus for platform delivery."""
        from aja.runtime.event_bus import bus, EVENTS

        bus.publish(EVENTS["MISSION_COMPLETED"], {"message": text})
