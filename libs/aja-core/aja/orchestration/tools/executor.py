import os
import json
import logging
import shlex
import asyncio
import threading
from typing import List, Dict, Any, Optional
from aja.config import PROJECT_ROOT
from aja.security.command_guard import classify_command
from aja.runtime.execution import ExecutionRequest, get_default_execution_manager

logger = logging.getLogger(__name__)

class ToolExecutor:
    """
    Safely executes tools (shell commands) requested by the LLM.
    Uses CommandStripper logic internally.
    """

    BLACKBOX_COMMANDS = {"rm -rf /", "mkfs", "dd", "shutdown", "reboot"}

    def __init__(self):
        self.history = []

    def _run_execution(self, command: str, cwd: str, workspace_mode: str = "direct") -> Dict[str, Any]:
        async def _run():
            return await get_default_execution_manager().run(
                ExecutionRequest(
                    command=command,
                    cwd=cwd,
                    timeout=30,
                    workspace_mode=workspace_mode,
                    metadata={"legacy_api": "ToolExecutor.execute"},
                )
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(_run())
        else:
            box: Dict[str, Any] = {}
            err: Dict[str, BaseException] = {}

            def runner():
                try:
                    box["result"] = asyncio.run(_run())
                except BaseException as exc:
                    err["error"] = exc

            thread = threading.Thread(target=runner, daemon=True)
            thread.start()
            thread.join()
            if err:
                raise err["error"]
            result = box["result"]

        return {
            "status": "success" if result.success else "failed",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "code": result.exit_code,
            "session_id": result.session_id,
            "manifest_path": result.manifest_path,
        }

    def execute(self, command: str, cwd: str = None, workspace_mode: str = "direct") -> Dict[str, Any]:
        """Executes a single command and returns the result."""
        logger.info(f"ToolExecutor: Executing '{command}'")
        
        # 1. Safety Check
        classification = classify_command(command)
        if classification["decision"] == "deny":
            return {"status": "error", "message": "Command blocked: " + "; ".join(classification["reasons"])}

        # 2. Preparation
        target_cwd = cwd or str(PROJECT_ROOT)
        
        try:
            output = self._run_execution(command, target_cwd, workspace_mode)
            self.history.append({"command": command, "result": output})
            return output
            
        except Exception as e:
            logger.error(f"Execution error: {e}")
            return {"status": "error", "message": str(e)}

    async def dispatch_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        trace_id: str,
        mission_id: Optional[str] = None,
        journal: Optional[Any] = None,
        dry_run: bool = False,
    ) -> List[Any]:
        from aja.orchestration.tools.native import NativeToolRegistry
        from aja.orchestration.activity_rt import ActivityRuntime
        from aja.orchestration.scheduler import ParallelActivityScheduler

        registry = NativeToolRegistry(engine=None)
        runtime = ActivityRuntime(journal=journal, dry_run=dry_run)
        activities = []
        for tc in tool_calls:
            activity = registry.dispatch(tc["tool"], tc.get("args", {}), trace_id)
            activity.mission_id = mission_id
            activities.append(activity)
        scheduler = ParallelActivityScheduler(runtime)
        batch = await scheduler.run_batch(activities)
        return batch.results
