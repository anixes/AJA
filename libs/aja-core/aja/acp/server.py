"""
server.py - Agent Client Protocol (ACP) Server for AJA.
======================================================
Implements the open Agent Client Protocol (co-designed by Zed and JetBrains)
over JSON-RPC 2.0 stdio, allowing Zed IDE to spawn and control AJA as an
in-editor AI agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import Any, Dict, Optional, TextIO

logger = logging.getLogger(__name__)

ACP_PROTOCOL_VERSION = "2024-11-05"


class ACPServer:
    """
    JSON-RPC 2.0 ACP Server handling stdio communication with editor clients.
    """

    def __init__(
        self,
        in_stream: Optional[TextIO] = None,
        out_stream: Optional[TextIO] = None,
        gateway=None,
        tools_registry=None,
        executor=None,
        dry_run: bool = False,
    ):
        self.in_stream = in_stream or sys.stdin
        self.out_stream = out_stream or sys.stdout
        self.gateway = gateway
        self.tools_registry = tools_registry
        self.executor = executor
        self.dry_run = dry_run
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.active_tasks: Dict[str, asyncio.Task] = {}

    def _resolve_components(self):
        if self.gateway is None:
            from aja.orchestration.gateway import LLMGateway

            self.gateway = LLMGateway()
        if self.tools_registry is None:
            from aja.orchestration.tools.native import NativeToolRegistry

            self.tools_registry = NativeToolRegistry(engine=None)
        if self.executor is None:
            from aja.runtime.execution.tool_executor import ToolExecutor

            self.executor = ToolExecutor()

    def send_notification(self, method: str, params: Dict[str, Any]):
        """Send a JSON-RPC notification (no id)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        raw = json.dumps(msg, ensure_ascii=False)
        self.out_stream.write(raw + "\n")
        self.out_stream.flush()

    def send_response(self, req_id: Any, result: Any = None, error: Any = None):
        """Send a JSON-RPC response."""
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        raw = json.dumps(msg, ensure_ascii=False)
        self.out_stream.write(raw + "\n")
        self.out_stream.flush()

    async def handle_message(self, raw_line: str) -> Optional[Dict[str, Any]]:
        """Parse and route a single JSON-RPC message. Returns the response dict if request."""
        raw_line = raw_line.strip()
        if not raw_line:
            return None

        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError as e:
            err = {"code": -32700, "message": f"Parse error: {e}"}
            return {"jsonrpc": "2.0", "id": None, "error": err}

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        try:
            if method == "initialize":
                res = self._handle_initialize(params)
                return {"jsonrpc": "2.0", "id": req_id, "result": res}
            elif method == "session/new":
                res = self._handle_session_new(params)
                return {"jsonrpc": "2.0", "id": req_id, "result": res}
            elif method == "session/prompt":
                res = await self._handle_session_prompt(params)
                return {"jsonrpc": "2.0", "id": req_id, "result": res}
            elif method == "session/cancel":
                res = self._handle_session_cancel(params)
                return {"jsonrpc": "2.0", "id": req_id, "result": res}
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
        except Exception as e:
            logger.exception("Error handling ACP method %s: %s", method, e)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": f"Internal error: {e}"},
            }

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handshake: negotiate version and declare capabilities."""
        client_info = params.get("clientInfo", {})
        logger.info("ACP Client connected: %s", client_info)
        return {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "agentInfo": {
                "name": "AJA",
                "version": "0.2.0",
                "vendor": "Antigravity/AJA",
            },
            "capabilities": {
                "prompts": True,
                "sessions": True,
                "streaming": True,
                "tools": True,
            },
        }

    def _handle_session_new(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new session context bound to workspace roots."""
        session_id = f"acp-{uuid.uuid4().hex[:8]}"
        workspace_folders = params.get("workspaceFolders", [])
        self.sessions[session_id] = {
            "id": session_id,
            "workspaceFolders": workspace_folders,
            "history": [],
        }
        return {"sessionId": session_id}

    async def _handle_session_prompt(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an interactive prompt in the session using AJA's direct loop."""
        session_id = params.get("sessionId")
        prompt_text = params.get("prompt", "")

        if not session_id or session_id not in self.sessions:
            raise ValueError(f"Unknown or missing sessionId: {session_id}")

        session = self.sessions[session_id]
        history = session["history"]

        self._resolve_components()

        # Emit start notification
        self.send_notification(
            "session/update",
            {
                "sessionId": session_id,
                "state": "running",
                "message": f"Processing prompt: {prompt_text[:60]}...",
            },
        )

        from aja.orchestration.direct_loop import run_direct_loop

        outcome = await run_direct_loop(
            prompt_text,
            gateway=self.gateway,
            tools_registry=self.tools_registry,
            executor=self.executor,
            session_history=history,
            auto_verify=True,
            dry_run=self.dry_run,
        )

        # Emit completion notification
        last_response = ""
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                last_response = msg.get("content", "")
                break

        self.send_notification(
            "session/update",
            {
                "sessionId": session_id,
                "state": "idle",
                "message": "Turn finished.",
            },
        )

        return {
            "sessionId": session_id,
            "status": outcome.get("status") if outcome else "completed",
            "verified": outcome.get("verified", False) if outcome else False,
            "turns": outcome.get("turns", 1) if outcome else 1,
            "response": last_response,
        }

    def _handle_session_cancel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Cancel an ongoing prompt task."""
        session_id = params.get("sessionId")
        if session_id and session_id in self.active_tasks:
            task = self.active_tasks[session_id]
            task.cancel()
            return {"sessionId": session_id, "status": "cancelled"}
        return {"sessionId": session_id, "status": "not_running"}

    async def run_stdio(self):
        """Run the stdio message loop continuously until EOF."""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, self.in_stream)

        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                break  # EOF: editor closed pipe

            line = line_bytes.decode("utf-8", errors="replace")
            resp = await self.handle_message(line)
            if resp is not None:
                self.out_stream.write(json.dumps(resp, ensure_ascii=False) + "\n")
                self.out_stream.flush()
