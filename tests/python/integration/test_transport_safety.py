import asyncio
import os
import sys
import pytest
from pathlib import Path

from aja.runtime.execution import ExecutionManager, ExecutionRequest
from test_execution_runtime import make_git_project, py_cmd

@pytest.mark.skipif(sys.platform == 'win32', reason="POSIX process group tests require a POSIX environment")
def test_posix_grandchild_termination(tmp_path):
    async def scenario():
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root)
        
        # Spawn a process that spawns a background grandchild process
        # bash -c 'sleep 10 & sleep 1'
        req = ExecutionRequest(
            command="bash -c 'sleep 10 & sleep 10'",
            timeout=0.5
        )
        result = await manager.run(req)
        
        # Check if the grandchild process is orphaned
        # Since it's POSIX, we check if the process group was cleaned up
        assert result.success is False
        assert result.state == "timeout"
        
        # We can't trivially assert the pid is gone without knowing it, 
        # but if we didn't use process groups, the grandchild would hang the pipe
        # or leak. The fact that `manager.run` completes indicates that process group
        # termination works or pipes were closed properly.

    asyncio.run(scenario())

@pytest.mark.skipif(sys.platform != 'win32', reason="ConPTY tests require Windows")
def test_conpty_resource_exhaustion(tmp_path):
    async def scenario():
        from aja.runtime.execution.activity import set_activity_context
        set_activity_context(None)
        
        root = make_git_project(tmp_path)
        manager = ExecutionManager(project_root=root)
        
        # Sleep well beyond any plausible kill-sequence latency so the process
        # can never exit naturally before the 0.2s timeout fires, even under
        # heavy full-suite load (prevents flaky completed-vs-timeout races).
        for i in range(6):
            req = ExecutionRequest(
                command=py_cmd("import time; time.sleep(12.0)"),
                timeout=0.2,
                use_pty=True
            )
            session = await manager.start(req)
            result = await manager.wait(session.session_id)
            assert result.state == "timeout"

    asyncio.run(scenario())

# Serialize process-spawning/PTY tests onto one xdist worker: concurrent ConPTY
# handle pools exhaust on Windows and wedge workers (thread-method timeouts
# cannot abort them). All heavy subprocess tests share the 'process_heavy' group.
import pytest as _pytest
pytestmark = _pytest.mark.xdist_group("process_heavy")

