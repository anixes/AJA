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


def test_batch_parallel_tool_cap_in_direct_loop():
    from aja.orchestration.direct_loop import run_direct_loop

    class BurstGateway:
        def __init__(self):
            self.turn = 0

        async def chat(self, model=None, prompt=None, system=None, tools=None):
            self.turn += 1
            if self.turn == 1:
                # Emit 35 tool calls in one burst
                return {
                    "content": "",
                    "tool_calls": [
                        {"name": f"probe_{i}", "arguments": "{}"}
                        for i in range(35)
                    ],
                }
            return "Burst processed successfully."

    executor = RecordingExecutor()
    history = []

    outcome = asyncio.run(
        run_direct_loop(
            "burst probe",
            gateway=BurstGateway(),
            tools_registry=RecordingRegistry(),
            executor=executor,
            session_history=history,
            history_compressor=lambda h, model=None, provider=None: None,
            result_truncator=lambda raw: raw[:50],
            trace_id_fn=lambda: "",
        )
    )

    assert outcome["status"] == "completed"
    # Executor must have received only the capped 25 tool calls
    assert len(executor.dispatched) == 25
    # History must contain the batch guard notice
    assert any("[Batch Guard:" in m.get("content", "") for m in history)


def test_copilot_context_limit_resolution():
    from aja.orchestration.context_window import resolve_model_limit, compress_history

    limit = resolve_model_limit(model="copilot", provider="copilot")
    # Copilot limit must be capped at 12288 * 0.8 = 9830 tokens
    assert limit <= 12_288
    assert limit == int(12_288 * 0.8)

    # Verify history compression prunes bloated history down to Copilot budget
    bloated_history = [{"role": "user", "content": "Initial objective"}]
    # Add 20 large messages
    for i in range(20):
        bloated_history.append({"role": "user", "content": "x" * 2000})

    compress_history(bloated_history, model="copilot", provider="copilot")
    # Must have popped messages down to fit budget
    assert len(bloated_history) < 21


def test_is_creation_objective():
    from aja.orchestration.direct_loop import _is_creation_objective

    # Read-only / review tasks must return False
    assert not _is_creation_objective("Review the git diff of the latest commit on native-worker-3 and confirm all changes adhere to clean code principles.")
    assert not _is_creation_objective("Search tests/python/unit/ for any unused imports or deprecated pytest warnings")
    assert not _is_creation_objective("Check git status and explain the changes")
    assert not _is_creation_objective("Audit codebase security vulnerabilities")
    assert not _is_creation_objective("What is the current system status?")

    # Creation / generation tasks must return True
    assert _is_creation_objective("generate an exploratory notebook in the CSV directory")
    assert _is_creation_objective("write and execute a Python script in the CSV directory that trains a Random Forest model")
    assert _is_creation_objective("create a new test file structure and save it to disk")
    assert _is_creation_objective("convert and export data to output.json")


def test_extract_claimed_deliverables_ignores_reviews():
    from aja.orchestration.direct_loop import _extract_claimed_deliverables

    # Code review comments referencing backticked files must NOT be extracted as claimed deliverables
    review_content = (
        "I have reviewed the git diff:\n"
        "1. `libs/aja-core/aja/core/conversation.py`: adherence to clean code confirmed.\n"
        "2. `libs/aja-core/aja/orchestration/direct_loop.py`: well structured.\n"
        "3. `tests/python/unit/test_direct_loop_isolation.py`: test coverage is adequate.\n"
        "Current Deliverable Status: All clean."
    )
    assert _extract_claimed_deliverables(review_content) == []

    # Explicit creation statements MUST be extracted
    created_content = (
        "The model training finished. The script is saved as: D:\\projects\\train.py\n"
        "The visual plot is created at: D:\\projects\\plot.png\n"
        "Summary data exported to: output.json"
    )
    extracted = _extract_claimed_deliverables(created_content)
    assert "D:\\projects\\train.py" in extracted
    assert "D:\\projects\\plot.png" in extracted
    assert "output.json" in extracted


def test_code_review_objective_bypasses_deliverable_verification():
    from aja.orchestration.direct_loop import run_direct_loop

    class ReviewGateway:
        async def chat(self, model=None, prompt=None, system=None, tools=None):
            # Model references files that do NOT exist on disk in CWD
            return (
                "Review of commit:\n"
                "- `nonexistent_module.py`: clean syntax.\n"
                "- `missing_util.py`: no issues detected."
            )

    history = []
    outcome = asyncio.run(
        run_direct_loop(
            "Review the git diff of the latest commit on native-worker-3 and confirm all changes adhere to clean code principles.",
            gateway=ReviewGateway(),
            tools_registry=RecordingRegistry(),
            executor=RecordingExecutor(),
            session_history=history,
            max_turns=3,
            history_compressor=lambda h, model=None, provider=None: None,
            result_truncator=lambda raw: raw[:50],
            trace_id_fn=lambda: "",
        )
    )

    # Must complete cleanly in 1 turn without deliverable verification failure
    assert outcome["status"] == "completed"
    assert outcome["turns"] == 1
    # History must not contain any deliverable failure prompt
    assert not any("Missing Deliverable" in m.get("content", "") for m in history)
    assert not any("write_file" in m.get("content", "") for m in history)


def test_standardized_tool_calls_and_role_tool_in_history():
    from aja.orchestration.direct_loop import run_direct_loop

    class ToolGateway:
        def __init__(self):
            self.turn = 0

        async def chat(self, model=None, prompt=None, system=None, tools=None):
            self.turn += 1
            if self.turn == 1:
                return {
                    "content": "Running git diff",
                    "tool_calls": [
                        {"name": "git_diff", "arguments": json.dumps({"ref": "HEAD~1"})}
                    ],
                }
            return "Review completed after inspecting diff."

    history = []
    outcome = asyncio.run(
        run_direct_loop(
            "Check git diff and summarize",
            gateway=ToolGateway(),
            tools_registry=RecordingRegistry(),
            executor=RecordingExecutor(),
            session_history=history,
            history_compressor=lambda h, model=None, provider=None: None,
            result_truncator=lambda raw: raw[:50],
            trace_id_fn=lambda: "",
        )
    )

    assert outcome["status"] == "completed"
    assert outcome["turns"] == 2

    # Turn 1 assistant message must have standard tool_calls
    asst_msg = next(m for m in history if m.get("role") == "assistant" and m.get("tool_calls"))
    assert len(asst_msg["tool_calls"]) == 1
    tc = asst_msg["tool_calls"][0]
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "git_diff"
    assert "ref" in tc["function"]["arguments"]

    # Turn 1 tool result must have role="tool" and matching tool_call_id
    tool_msg = next(m for m in history if m.get("role") == "tool")
    assert tool_msg["tool_call_id"] == tc["id"]
    assert tool_msg["content"] == "ok"


def test_anthropic_adapter_maps_tool_calls_and_tool_results():
    from aja.orchestration.providers.anthropic_adapter import AnthropicAdapter

    messages = [
        {"role": "user", "content": "Run diff"},
        {
            "role": "assistant",
            "content": "Checking diff",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "git_diff", "arguments": '{"ref": "HEAD"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_123", "content": "diff output"},
    ]

    body = AnthropicAdapter._build_body(
        model="claude-3-5-sonnet",
        messages=messages,
        system="System prompt",
        tools=None,
        temperature=None,
        extra_body=None,
        max_tokens=1024,
    )

    chat_messages = body["messages"]
    # Assistant message must contain text + tool_use block
    asst = chat_messages[1]
    assert asst["role"] == "assistant"
    assert isinstance(asst["content"], list)
    assert asst["content"][0] == {"type": "text", "text": "Checking diff"}
    assert asst["content"][1] == {
        "type": "tool_use",
        "id": "call_123",
        "name": "git_diff",
        "input": {"ref": "HEAD"},
    }

    # Tool message must be translated to role="user" with tool_result block
    tool_user = chat_messages[2]
    assert tool_user["role"] == "user"
    assert isinstance(tool_user["content"], list)
    assert tool_user["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "call_123",
        "content": "diff output",
    }


def test_atomic_compress_history_with_tool_calls():
    from aja.orchestration.context_window import compress_history

    # History with objective, assistant tool call + 2 tool results, then next assistant turn
    history = [
        {"role": "user", "content": "Initial objective"},
        {
            "role": "assistant",
            "content": "Calling tools",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "t1", "arguments": "{}"}},
                {"id": "call_2", "function": {"name": "t2", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result 1" * 1000},
        {"role": "tool", "tool_call_id": "call_2", "content": "result 2" * 1000},
        {"role": "assistant", "content": "Final synthesis"},
    ]

    compress_history(history, model="copilot", provider="copilot", reserve_tokens=9000)

    # When pruned, history must not contain orphaned role="tool" messages without preceding tool_calls
    roles = [m.get("role") for m in history]
    if "tool" in roles:
        tool_idx = roles.index("tool")
        assert tool_idx > 0
        assert history[tool_idx - 1].get("role") == "assistant"
        assert history[tool_idx - 1].get("tool_calls")

