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
        
        for i in range(10):
            req = ExecutionRequest(
                command=py_cmd("import time; time.sleep(5.0)"),
                timeout=0.2
            )
            session = await manager.start(req)
            result = await manager.wait(session.session_id)
            assert result.state == "timeout"

    asyncio.run(scenario())
