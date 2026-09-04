"""
test_acp_protocol.py - Unit tests for Agent Client Protocol (ACP) server.
=========================================================================
"""

import asyncio
import io
import json
from aja.acp.server import ACPServer, ACP_PROTOCOL_VERSION


class MockACPGateway:
    def __init__(self, response="Task complete."):
        self.response = response
        self.provider = "mock"

    async def chat(self, model=None, prompt=None, system=None, tools=None):
        return self.response


class MockACPRegistry:
    def get_schemas(self, interactive=True):
        return []


class MockACPExecutor:
    async def dispatch_tool_calls(self, tool_calls, trace_id, dry_run=False):
        return []


def test_acp_initialize_handshake():
    async def _run():
        out_buf = io.StringIO()
        server = ACPServer(out_stream=out_buf)

        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "zed", "version": "0.150.0"},
            },
        }

        resp = await server.handle_message(json.dumps(req))
        assert resp is not None
        assert resp["id"] == 1
        assert "result" in resp
        assert resp["result"]["protocolVersion"] == ACP_PROTOCOL_VERSION
        assert resp["result"]["agentInfo"]["name"] == "AJA"
        assert resp["result"]["capabilities"]["sessions"] is True

    asyncio.run(_run())


def test_acp_session_lifecycle_and_prompt():
    async def _run():
        out_buf = io.StringIO()
        server = ACPServer(
            out_stream=out_buf,
            gateway=MockACPGateway("I updated the file, Sir."),
            tools_registry=MockACPRegistry(),
            executor=MockACPExecutor(),
            dry_run=True,
        )

        # 1. Initialize
        await server.handle_message(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        )

        # 2. session/new
        new_session_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "workspaceFolders": [{"uri": "file:///workspace", "name": "project"}]
            },
        }
        resp = await server.handle_message(json.dumps(new_session_req))
        assert resp["id"] == 2
        session_id = resp["result"]["sessionId"]
        assert session_id in server.sessions

        # 3. session/prompt
        prompt_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": "Fix bug in module",
            },
        }
        prompt_resp = await server.handle_message(json.dumps(prompt_req))
        assert prompt_resp["id"] == 3
        assert prompt_resp["result"]["status"] == "completed"
        assert prompt_resp["result"]["response"] == "I updated the file, Sir."

        # Verify notifications were emitted to out_stream
        notifications = [
            json.loads(line) for line in out_buf.getvalue().splitlines() if line.strip()
        ]
        methods = [n.get("method") for n in notifications]
        assert "session/update" in methods

    asyncio.run(_run())


def test_acp_parse_and_method_errors():
    async def _run():
        server = ACPServer()

        # Malformed JSON
        resp = await server.handle_message("{broken json")
        assert resp["error"]["code"] == -32700

        # Unknown method
        resp = await server.handle_message(
            json.dumps({"jsonrpc": "2.0", "id": 99, "method": "non_existent_method"})
        )
        assert resp["error"]["code"] == -32601
        assert "Method not found" in resp["error"]["message"]

    asyncio.run(_run())
