import pytest
import asyncio
import threading
from pathlib import Path
from aja.observability.telemetry import (
    TraceContextManager,
    get_trace_id,
    set_trace_id,
    _trace_id_ctx
)
from aja.runtime.handover import BatonManager

def test_trace_context_manager():
    # Clear any active trace ID
    _trace_id_ctx.set(None)
    
    initial = get_trace_id()
    assert initial.startswith("tr-")
    
    with TraceContextManager("tr-custom123") as tid:
        assert tid == "tr-custom123"
        assert get_trace_id() == "tr-custom123"
        
    # Should revert back to initial or a generated one (since context variable is reset)
    assert get_trace_id() != "tr-custom123"

def test_trace_isolation_threads():
    results = {}
    
    def run_thread(name, trace_val):
        with TraceContextManager(trace_val):
            # simulate some work
            import time
            time.sleep(0.1)
            results[name] = get_trace_id()
            
    t1 = threading.Thread(target=run_thread, args=("t1", "tr-thread1"))
    t2 = threading.Thread(target=run_thread, args=("t2", "tr-thread2"))
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    assert results["t1"] == "tr-thread1"
    assert results["t2"] == "tr-thread2"

@pytest.mark.anyio
async def test_trace_isolation_async():
    results = {}
    
    async def run_task(name, trace_val):
        with TraceContextManager(trace_val):
            import anyio
            await anyio.sleep(0.05)
            results[name] = get_trace_id()
            
    import anyio
    async with anyio.create_task_group() as tg:
        tg.start_soon(run_task, "a1", "tr-async1")
        tg.start_soon(run_task, "a2", "tr-async2")
    
    assert results["a1"] == "tr-async1"
    assert results["a2"] == "tr-async2"

def test_trace_propagation_via_baton(tmp_path):
    manager = BatonManager()
    manager.baton_dir = tmp_path
    
    objective = "Verify trace ID round-trips via Arrow Baton IPC"
    state = {
        "run_id": "test_trace_propagation_run",
        "history": [],
        "metadata": {"some_key": "some_value"}
    }
    
    # 1. Set a trace ID and capture baton
    with TraceContextManager("tr-propagated123") as active_tid:
        code = manager.capture(objective, state)
        
    # 2. Reset context
    _trace_id_ctx.set(None)
    assert get_trace_id() != "tr-propagated123"
    
    # 3. Pickup baton and assert trace ID was restored
    thawed = manager.pickup(code)
    assert thawed is not None
    assert thawed["metadata"]["trace_id"] == "tr-propagated123"
    assert get_trace_id() == "tr-propagated123"
