"""
Night-shift Wave 1 — E2b regression tests.

Covers:
1. remote_control uses the async intent parser (no sync LLM call on the loop).
2. ToolExecutor.execute_async / NativeToolRegistry.execute_async non-blocking paths.
3. adapters.dispatch_worker offloads blocking adapter.run() off the loop.
4. run_direct_loop executes commands without the sync-over-async thread bridge.
5. LLMGateway.chat closes per-call provider adapters (fd/socket leak fix).
6. llm.run_async_synchronously hardening (BaseException + dead-thread safety).
"""

import asyncio
import threading
import pytest
from unittest.mock import patch, MagicMock

import aja.gateway.remote_control as remote_control
from aja.gateway.remote_control import execute_local_control


pytestmark = [pytest.mark.timeout(120), pytest.mark.anyio]


# ---------------------------------------------------------------------------
# 1. remote_control: async intent parser on the /pc path
# ---------------------------------------------------------------------------

async def test_execute_local_control_uses_async_intent_parser():
    """The sync parse_intent (full LLM roundtrip) must not run on the loop."""
    calls = {"sync": 0, "async": 0}

    def fake_sync(*args, **kwargs):
        calls["sync"] += 1
        return {"type": "question", "response": "sync"}

    async def fake_async(message, history, system_state=None):
        calls["async"] += 1
        assert message == "what files changed today"
        return {"type": "question", "response": "async-reply", "confidence": 0.9}

    with patch.object(remote_control, "parse_intent_async", side_effect=fake_async), \
         patch("aja.interface.intent_parser.parse_intent", side_effect=fake_sync), \
         patch.object(remote_control, "_system_state", return_value={}):
        result = await execute_local_control("what files changed today")

    assert calls["sync"] == 0, "sync parse_intent froze the event loop"
    assert calls["async"] == 1
    assert "async-reply" in result


async def test_execute_local_control_tool_calls_path_unchanged():
    """Tool-call intents still dispatch through ToolExecutor.dispatch_tool_calls."""

    class FakeResult:
        success = True
        tool = "list_dir"
        data = "a.txt"
        error = None

    class FakeExecutor:
        def __init__(self):
            pass

        async def dispatch_tool_calls(self, **kwargs):
            return [FakeResult()]

    async def fake_async(message, history, system_state=None):
        return {
            "type": "tool_calls",
            "tool_calls": [{"tool": "list_dir", "args": {}}],
            "response": "On it.",
        }

    with patch.object(remote_control, "parse_intent_async", side_effect=fake_async), \
         patch.object(remote_control, "_system_state", return_value={}), \
         patch("aja.orchestration.tools.executor.ToolExecutor", FakeExecutor), \
         patch("aja.runtime.mission_journal.MissionJournal", MagicMock()):
        result = await execute_local_control("list my files")

    assert "Local PC execution complete" in result
    assert "[OK] list_dir" in result


# ---------------------------------------------------------------------------
# 2. ToolExecutor / NativeToolRegistry async execution paths
# ---------------------------------------------------------------------------

async def test_tool_executor_execute_async_runs_native(monkeypatch):
    """execute_async must await ExecutionManager.run directly (no thread bridge)."""
    from aja.orchestration.tools.executor import ToolExecutor

    seen = {}

    class FakeResult:
        success = True
        stdout = " hello \n"
        stderr = ""
        exit_code = 0
        session_id = "s1"
        manifest_path = None

    class FakeManager:
        async def run(self, request):
            seen["request"] = request
            return FakeResult()

    monkeypatch.setattr(
        "aja.orchestration.tools.executor.get_default_execution_manager",
        lambda: FakeManager(),
    )

    ex = ToolExecutor()
    result = await ex.execute_async("echo hi", cwd=".")

    req = seen["request"]
    assert req.command == "echo hi"
    assert req.metadata.get("legacy_api") == "ToolExecutor.execute_async"
    assert result["status"] == "success"
    assert result["stdout"] == "hello"


async def test_tool_executor_execute_async_respects_deny():
    from aja.orchestration.tools.executor import ToolExecutor

    ex = ToolExecutor()
    result = await ex.execute_async("rm -rf /")
    assert result["status"] == "error"
    assert "blocked" in result["message"].lower()


async def test_native_registry_execute_async_matches_execute():
    from aja.orchestration.tools.native import NativeToolRegistry

    registry = NativeToolRegistry(engine=None)
    name = next(iter(registry.tools))
    args = {}
    # execute() may legitimately return a string OR raise ToolSignatureError
    # (signature-drift contract); execute_async must mirror whichever occurs.
    try:
        sync_out = registry.execute(name, args)
        sync_exc = None
    except Exception as exc:  # noqa: BLE001 - contract comparison
        sync_out = None
        sync_exc = exc
    try:
        async_out = await registry.execute_async(name, args)
        async_exc = None
    except Exception as exc:  # noqa: BLE001 - contract comparison
        async_out = None
        async_exc = exc
    assert (sync_out is None) == (async_out is None)
    if sync_out is not None:
        assert isinstance(sync_out, str)
        assert isinstance(async_out, str)
    else:
        assert type(async_exc) is type(sync_exc)


async def test_native_registry_execute_async_does_not_block_loop():
    """A slow tool executed via execute_async must keep the loop responsive."""
    from aja.orchestration.tools.native import NativeToolRegistry

    registry = NativeToolRegistry(engine=None)

    def slow_tool():
        import time

        time.sleep(0.5)
        return "done"

    registry.tools["__test_slow__"] = slow_tool

    heartbeats = []

    async def heartbeat():
        for _ in range(12):
            heartbeats.append(1)
            await asyncio.sleep(0.05)

    hb_task = asyncio.create_task(heartbeat())
    out = await registry.execute_async("__test_slow__", {})
    await hb_task

    assert out == "done"
    # If execute() had been called inline, the loop would have frozen and the
    # heartbeat count would be ~0-1 instead of progressing during execution.
    assert len(heartbeats) >= 5


# ---------------------------------------------------------------------------
# 3. adapters.dispatch_worker offloads blocking adapter.run()
# ---------------------------------------------------------------------------

async def test_dispatch_worker_offloads_sync_adapter_to_thread(monkeypatch):
    from aja.orchestration import adapters as adapters_mod

    seen = {}
    done = threading.Event()

    class FakeAdapter:
        def run(self, baton, workspace_dir):
            seen["thread"] = threading.current_thread()
            import time

            time.sleep(0.3)  # blocking work
            return {"status": "completed", "output": "ok"}

    monkeypatch.setattr(adapters_mod, "SwarmMaintenanceAdapter", FakeAdapter)

    loop_thread = threading.current_thread()
    heartbeats = []

    async def heartbeat():
        while not done.is_set():
            heartbeats.append(1)
            await asyncio.sleep(0.05)

    hb_task = asyncio.create_task(heartbeat())
    result = await adapters_mod.dispatch_worker("unknown-worker", {"task": "t"}, ".")
    done.set()
    await hb_task

    assert result["status"] == "completed"
    assert seen["thread"] is not loop_thread, "blocking run() executed on the loop"
    assert len(heartbeats) >= 3, "event loop was frozen by adapter.run()"


async def test_dispatch_worker_native_path_still_async(monkeypatch):
    from aja.orchestration import adapters as adapters_mod

    class NativeAdapter:
        def __init__(self):
            self.called = False

        async def run_async(self, baton, workspace_dir):
            self.called = True
            return {"status": "completed"}

    inst = NativeAdapter()
    monkeypatch.setattr(adapters_mod, "NativeWorkerAdapter", lambda: inst)
    result = await adapters_mod.dispatch_worker("native-worker", {}, ".")

    assert inst.called
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 4. run_direct_loop: no sync-over-async bridge at the command site
# ---------------------------------------------------------------------------

class _FakeGateway:
    """Two-turn gateway: first turn suggests a bash command, second finishes."""

    def __init__(self):
        self.turns = 0

    async def chat(self, model=None, prompt=None, system=None, tools=None):
        self.turns += 1
        if self.turns == 1:
            return "Working.\n```bash\necho wave1-e2b-probe\n```"
        return "All done."


class _FakeRegistry:
    def get_schemas(self, interactive=True):
        return []


class _RecordingExecutor:
    """Duck-typed executor exposing BOTH paths so we can assert which ran."""

    def __init__(self):
        self.sync_calls = 0
        self.async_cmds = []

    def execute(self, cmd, cwd=None, workspace_mode="direct"):
        self.sync_calls += 1
        return {"status": "success", "stdout": "", "stderr": "", "code": 0}

    async def execute_async(self, cmd, cwd=None, workspace_mode="direct"):
        self.async_cmds.append(cmd)
        return {"status": "success", "stdout": "wave1-e2b-probe", "stderr": "", "code": 0}


async def test_run_direct_loop_prefers_execute_async():
    from aja.orchestration.direct_loop import run_direct_loop

    executor = _RecordingExecutor()
    result = await run_direct_loop(
        "probe",
        gateway=_FakeGateway(),
        tools_registry=_FakeRegistry(),
        executor=executor,
        max_turns=5,
        dry_run=False,
        interactive=False,
    )

    assert result["status"] == "completed"
    assert executor.async_cmds == ["echo wave1-e2b-probe"]
    assert executor.sync_calls == 0, "direct loop used the loop-blocking sync bridge"


async def test_run_direct_loop_falls_back_to_thread_for_sync_executors():
    from aja.orchestration.direct_loop import run_direct_loop

    class SyncOnlyExecutor:
        def __init__(self):
            self.calls = []
            self.thread_id = None

        def execute(self, cmd, cwd=None, workspace_mode="direct"):
            self.calls.append(cmd)
            self.thread_id = threading.current_thread().ident
            return {"status": "success", "stdout": "", "stderr": "", "code": 0}

    executor = SyncOnlyExecutor()

    class OneShotGateway:
        def __init__(self):
            self.turns = 0

        async def chat(self, model=None, prompt=None, system=None, tools=None):
            self.turns += 1
            if self.turns == 1:
                return "```bash\necho fallback-probe\n```"
            return "done."

    result = await run_direct_loop(
        "probe",
        gateway=OneShotGateway(),
        tools_registry=_FakeRegistry(),
        executor=executor,
        max_turns=5,
        dry_run=False,
        interactive=False,
    )

    assert result["status"] == "completed"
    assert len(executor.calls) == 1
    # Executed OFF the loop thread (to_thread), so the loop stayed live.
    assert executor.thread_id != threading.current_thread().ident


# ---------------------------------------------------------------------------
# 5. Gateway adapter-path cleanup (per-call leak)
# ---------------------------------------------------------------------------

async def test_gateway_chat_closes_per_call_adapter():
    from aja.orchestration.providers import register_adapter, _REGISTRY
    from aja.orchestration.providers.base import LLMResponse
    from aja.orchestration.gateway import LLMGateway

    closed = {"count": 0}

    class DummyAdapter:
        provider_name = "dummy"

        def __init__(self, api_key="", base_url=""):
            pass

        async def chat(self, model, messages, system="", tools=None,
                       temperature=None, extra_body=None, retries=1):
            return LLMResponse(content="adapter-ok")

        async def close(self):
            closed["count"] += 1

    register_adapter("e2b-dummy", DummyAdapter)
    try:
        gw = LLMGateway(provider="e2b-dummy", api_key="k", base_url="http://localhost:9")
        out = await gw.chat(model="m1", prompt="hi")
        assert out == "adapter-ok"
        assert closed["count"] == 1, "adapter never closed -> leaked connection pool"

        await gw.chat(model="m1", prompt="hi again")
        assert closed["count"] == 2, "each chat() creates (and must close) an adapter"
    finally:
        _REGISTRY.pop("e2b-dummy", None)


async def test_gateway_chat_closes_adapter_even_when_chat_raises():
    from aja.orchestration.providers import register_adapter, _REGISTRY
    from aja.orchestration.gateway import LLMGateway

    closed = {"count": 0}

    class ExplodingAdapter:
        provider_name = "boom"

        def __init__(self, api_key="", base_url=""):
            pass

        async def chat(self, **kwargs):
            raise RuntimeError("provider down")

        async def close(self):
            closed["count"] += 1

    register_adapter("e2b-boom", ExplodingAdapter)
    try:
        gw = LLMGateway(provider="e2b-boom", api_key="k", base_url="http://localhost:9")
        # Adapter failure falls back to the legacy path, which with retries=0
        # yields None; what matters here: close() still ran before fallback.
        out = await gw.chat(model="m1", prompt="hi", retries=0)
        assert out is None
        assert closed["count"] == 1, "close() skipped on adapter failure"
    finally:
        _REGISTRY.pop("e2b-boom", None)


# ---------------------------------------------------------------------------
# 6. run_async_synchronously hardening
# ---------------------------------------------------------------------------

def test_run_async_synchronously_plain_loop():
    from aja.llm import run_async_synchronously

    async def coro():
        await asyncio.sleep(0.01)
        return 42

    assert run_async_synchronously(coro()) == 42


def test_run_async_synchronously_inside_running_loop_bridges():
    """When already on a loop it bridges via a worker thread and returns."""
    from aja.llm import run_async_synchronously

    async def coro():
        return "bridged"

    async def outer():
        return run_async_synchronously(coro())

    assert asyncio.run(outer()) == "bridged"


def test_run_async_synchronously_propagates_base_exception():
    """BaseException (e.g. CancelledError) inside the coroutine must surface,
    not deadlock the future."""
    from aja.llm import run_async_synchronously

    class Weird(BaseException):
        pass

    async def coro():
        raise Weird("boom")

    async def outer():
        return run_async_synchronously(coro())

    with pytest.raises(Weird):
        asyncio.run(outer())


def test_run_async_synchronously_worker_crash_does_not_hang(monkeypatch):
    """If the worker thread dies before resolving the future, we raise instead
    of hanging forever inside res_future.result()."""
    import aja.llm as llm_mod

    real_thread_cls = threading.Thread

    class DyingThread(real_thread_cls):
        def join(self, timeout=None):
            return  # simulate: thread vanished without setting the future

    monkeypatch.setattr(llm_mod.threading, "Thread", DyingThread)

    async def coro():
        return 1

    async def outer():
        return llm_mod.run_async_synchronously(coro())

    with pytest.raises(RuntimeError, match="without producing a result"):
        asyncio.run(outer())
