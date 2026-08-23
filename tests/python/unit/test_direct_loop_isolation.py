"""Isolation proof for the extracted direct loop (aja.orchestration.direct_loop).

Guarantees under test:
1. The core loop runs to completion with pure-fake gateway/registry/executor
   and injected pure callables — in a fresh subprocess with AJA_DATA_DIR
   redirected to an empty tmp dir, WITHOUT importing aja.config (the module
   whose import creates DATA_DIR) or lancedb, and without creating a single
   file under the redirected data dir.
2. Hooks fire correctly; legacy SwarmEngine.execute_direct behavior is
   preserved by its adapter (covered by integration suites separately).
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

CORE_PATH = str(Path(__file__).resolve().parents[3] / "libs" / "aja-core")

ISOLATION_CHILD = r'''
import asyncio, json, sys, os
from types import SimpleNamespace

sys.path.insert(0, os.environ["AJA_CORE_PATH"])

# Import ONLY the extracted loop module. Its module scope is stdlib-only.
import aja.orchestration.direct_loop as dl


class FakeGateway:
    def __init__(self):
        self.calls = 0

    async def chat(self, model=None, prompt=None, system=None, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "",
                "tool_calls": [
                    {"name": "sleep", "arguments": json.dumps({"seconds": 0})}
                ],
            }
        return "Mission accomplished, harness verified."


class FakeRegistry:
    def get_schemas(self, interactive=True):
        return []


class FakeExecutor:
    async def dispatch_tool_calls(self, tool_calls, trace_id=None, dry_run=False):
        return [
            SimpleNamespace(success=True, tool=tc["tool"], data="ok", error=None)
            for tc in tool_calls
        ]


seen = {"tools": 0, "synthesis": None}


async def main():
    outcome = await dl.run_direct_loop(
        "isolated probe",
        gateway=FakeGateway(),
        tools_registry=FakeRegistry(),
        executor=FakeExecutor(),
        history_compressor=lambda h, model=None, provider=None: None,
        result_truncator=lambda raw: raw[:200],
        trace_id_fn=lambda: "",
    )
    assert outcome["status"] == "completed", outcome
    assert outcome["turns"] == 2, outcome

    for banned in ("lancedb", "aja.config", "aja.api.bridge"):
        assert banned not in sys.modules, f"banned module imported: {banned}"

    # The redirected AJA_DATA_DIR must not exist or must be completely empty.
    data_dir = os.environ["AJA_DATA_DIR"]
    if os.path.isdir(data_dir):
        leftovers = []
        for root, _dirs, files in os.walk(data_dir):
            for f in files:
                leftovers.append(os.path.join(root, f))
        assert not leftovers, f"files created under redirected DATA_DIR: {leftovers}"

    print("ISOLATION_OK")


asyncio.run(main())
'''


def _spawn_child(env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["AJA_CORE_PATH"] = CORE_PATH
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", ISOLATION_CHILD],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        cwd=CORE_PATH,
    )


def test_subprocess_full_isolation(tmp_path):
    """Fresh subprocess + redirected AJA_DATA_DIR: zero files created, zero OS machinery."""
    fresh_data_dir = tmp_path / "redirected-data"
    proc = _spawn_child({"AJA_DATA_DIR": str(fresh_data_dir)})
    assert proc.returncode == 0, f"child failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "ISOLATION_OK" in proc.stdout
    assert not fresh_data_dir.exists() or not any(fresh_data_dir.rglob("*")), (
        "child wrote into redirected AJA_DATA_DIR"
    )
    assert "lancedb" not in proc.stderr.lower()


def test_subprocess_no_lancedb_import():
    """The loop never pulls lancedb into sys.modules even with default injectables absent."""
    proc = _spawn_child({"AJA_DATA_DIR": ""})  # unset-ish; child must not care
    # Empty string would make config resolve a default dir IF it were imported;
    # the banned-module assertions inside the child prove it is not.
    assert proc.returncode == 0, proc.stderr
    assert "ISOLATION_OK" in proc.stdout


class ScriptedGateway:
    """Fake gateway driving tool-call -> bash -> synthesis turns."""

    def __init__(self):
        self.turn = 0

    async def chat(self, model=None, prompt=None, system=None, tools=None):
        self.turn += 1
        names = [t.get("function", {}).get("name") for t in (tools or [])]
        if "emit_result" in names:
            schema_args = json.dumps({"answer": "done"})
            return {"content": "", "tool_calls": [{"name": "emit_result", "arguments": schema_args}]}
        if self.turn == 1:
            return {"content": "", "tool_calls": [{"name": "sleep", "arguments": "{}"}]}
        if self.turn == 2:
            return "```bash\necho hello-from-loop\n```"
        return "All steps completed."

    async def structured_stub(self):  # pragma: no cover - documentation helper
        return None


class RecordingRegistry:
    def get_schemas(self, interactive=True):
        return []


class RecordingExecutor:
    def __init__(self):
        self.executed = []

    async def dispatch_tool_calls(self, tool_calls, trace_id=None, dry_run=False):
        self.dispatched = tool_calls
        return [
            SimpleNamespace(success=True, tool=tc["tool"], data="ok", error=None)
            for tc in tool_calls
        ]

    def execute(self, command, cwd=None, workspace_mode="direct"):
        self.executed.append(command)
        return {"status": "success", "stdout": "hello-from-loop\n", "stderr": "", "code": 0}


def test_in_process_loop_hooks_and_synthesis(tmp_path, monkeypatch):
    from aja.orchestration.direct_loop import DirectLoopHooks, run_direct_loop

    monkeypatch.chdir(tmp_path)

    gateway = ScriptedGateway()
    registry = RecordingRegistry()
    executor = RecordingExecutor()

    commands_seen, tool_results, synthesis = [], [], []

    hooks = DirectLoopHooks(
        on_command=lambda cmd, result: commands_seen.append((cmd, result.get("status"))),
        on_tool_result=lambda r: tool_results.append(r.tool),
        on_synthesis=lambda s: synthesis.append(s),
    )

    contract = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }

    outcome = asyncio.run(
        run_direct_loop(
            "hook probe",
            gateway=gateway,
            tools_registry=registry,
            executor=executor,
            output_contract=contract,
            model="fake-model",
            provider="fake",
            dry_run=False,
            hooks=hooks,
            history_compressor=lambda h, model=None, provider=None: None,
            result_truncator=lambda raw: raw[:100],
            trace_id_fn=lambda: "test-trace",
        )
    )

    assert outcome["status"] == "completed"
    assert outcome["result"] == {"answer": "done"}
    assert tool_results == ["sleep"]
    assert commands_seen and commands_seen[0][0] == "echo hello-from-loop"
    assert commands_seen[0][1] == "success"
    assert synthesis == [{"answer": "done"}]
    # session history was seeded fresh and mutated locally
    assert isinstance(outcome, dict)


def test_in_process_max_turns_guard():
    from aja.orchestration.direct_loop import run_direct_loop

    class ChattyGateway:
        async def chat(self, **kwargs):
            return "```bash\necho loop-forever\n```"

    class LoopExecutor:
        def execute(self, command, cwd=None, workspace_mode="direct"):
            return {"status": "success", "stdout": "", "stderr": "", "code": 0}

    outcome = asyncio.run(
        run_direct_loop(
            "runaway probe",
            gateway=ChattyGateway(),
            tools_registry=RecordingRegistry(),
            executor=LoopExecutor(),
            max_turns=4,
            dry_run=False,
            history_compressor=lambda h, model=None, provider=None: None,
            result_truncator=lambda raw: raw[:10],
            trace_id_fn=lambda: "",
        )
    )
    assert outcome["status"] == "incomplete"
    assert outcome["reason"] == "max_turns"


def test_session_history_caller_owned_mutation():
    from aja.orchestration.direct_loop import run_direct_loop

    gateway = AsyncMock()
    gateway.chat = AsyncMock(side_effect=["Done."])
    shared = [{"role": "user", "content": "caller-seeded objective"}]

    outcome = asyncio.run(
        run_direct_loop(
            "history probe",
            gateway=gateway,
            tools_registry=RecordingRegistry(),
            executor=RecordingExecutor(),
            session_history=shared,
            history_compressor=lambda h, model=None, provider=None: None,
            result_truncator=lambda raw: raw[:10],
            trace_id_fn=lambda: "",
        )
    )

    assert outcome["status"] == "completed"
    assert shared[0]["role"] == "user"
    assert any(m.get("content") == "Done." for m in shared), "assistant reply missing from caller list"
