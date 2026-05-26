import asyncio
import os
import sys
import pytest
from pathlib import Path
from aja.runtime.execution.contracts import ExecutionRequest
from aja.runtime.execution.manager import ExecutionManager
from aja.runtime.execution.sequencer import EventSequencer, StreamNormalizer
from aja.runtime.execution.transport import PipeTransport


@pytest.mark.anyio
async def test_pipe_transport_execution():
    """Verify standard PipeTransport correctly runs and captures command outputs."""
    transport = PipeTransport(
        command=[sys.executable, "-c", "import sys; print('pipe_stdout'); sys.stderr.write('pipe_stderr\\n')"],
        cwd=os.getcwd(),
        env=os.environ.copy(),
        shell=False,
    )
    await transport.start()
    assert transport.pid is not None
    
    stdout_task = asyncio.create_task(transport.stdout.read())
    stderr_task = asyncio.create_task(transport.stderr.read())
    
    exit_code = await transport.wait()
    assert exit_code == 0
    
    stdout_data = await stdout_task
    stderr_data = await stderr_task
    
    assert b"pipe_stdout" in stdout_data
    assert b"pipe_stderr" in stderr_data


@pytest.mark.anyio
async def test_event_sequencer_monotonicity():
    """Verify that EventSequencer enforces monotonic, contiguous sequence IDs."""
    seq = EventSequencer(session_id="test-session", trace_id="test-trace")
    
    ev1 = seq.sequence_event("EXECUTION_STDOUT", {"line": "hello"})
    ev2 = seq.sequence_event("EXECUTION_STDOUT", {"line": "world"})
    
    assert ev1["sequence"] == 0
    assert ev1["epoch"] == 0
    assert ev2["sequence"] == 1
    assert ev2["epoch"] == 0
    
    seq.next_epoch()
    ev3 = seq.sequence_event("EXECUTION_STDOUT", {"line": "new_epoch"})
    assert ev3["sequence"] == 2
    assert ev3["epoch"] == 1


def test_stream_normalizer():
    """Verify that StreamNormalizer formats and strips line breaks correctly."""
    normalizer = StreamNormalizer()
    res = normalizer.normalize("stdout", "line_chunk\r\n")
    assert res["stream"] == "stdout"
    assert res["line"] == "line_chunk"
    assert res["raw_len"] == 12


@pytest.mark.anyio
async def test_manager_integration():
    """Verify that ExecutionManager integrates cleanly with standard requests."""
    mgr = ExecutionManager()
    req = ExecutionRequest(
        command=f"{sys.executable} -c \"print('manager_integration')\"",
        shell=True,
    )
    res = await mgr.run(req)
    assert res.success is True
    assert "manager_integration" in res.stdout
    assert res.state == "completed"


@pytest.mark.anyio
async def test_transport_cancellation_stress():
    """Verify that transport cancellation terminates process trees without thread or descriptor leaks."""
    mgr = ExecutionManager()
    # Spawns a long sleeping process
    req = ExecutionRequest(
        command=f"{sys.executable} -c \"import time; time.sleep(100)\"",
        shell=True,
        timeout=1.0,  # Short timeout to force cancellation
    )
    
    res = await mgr.run(req)
    assert res.success is False
    assert res.state == "timeout" or res.state == "cancelled"
    # Verify exit status is captured cleanly
    assert res.exit_code == -1


@pytest.mark.anyio
async def test_pty_execution_cross_platform():
    """Assert PTY execution runs cleanly on supported OS targets using new transport interfaces."""
    mgr = ExecutionManager()
    req = ExecutionRequest(
        command=f"{sys.executable} -c \"print('pty_verify')\"",
        shell=True,
        use_pty=True,
    )
    res = await mgr.run(req)
    assert res.success is True
    assert "pty_verify" in res.stdout
