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

    def _check_permission(self, command: str) -> Optional[Dict[str, Any]]:
        """Returns an error dict when the command must not run, else None."""
        classification = classify_command(command)
        if classification["decision"] == "deny":
            return {"status": "error", "message": "Command blocked: " + "; ".join(classification["reasons"])}
        elif classification["decision"] == "ask":
            from aja.config import CONFIG
            sandbox = getattr(CONFIG.swarm_settings, "sandbox_mode", "local")
            auto_proceed = getattr(CONFIG.swarm_settings, "auto_proceed_local", False)
            if sandbox == "local" and auto_proceed:
                granted = True
            else:
                scope = "shell.exec.dangerous"
                reason = f"Dangerous command requested: {command}\nReasons: {', '.join(classification['reasons'])}"
                from aja.security.permissions import PermissionEngine
                result = PermissionEngine().authorize(scope, reason=reason)
                granted = result.allowed

            if not granted:
                return {"status": "error", "message": "Command blocked by security policy: " + "; ".join(classification["reasons"])}
        return None

    @staticmethod
    def _resolve_cwd(cwd: str = None) -> str:
        if cwd:
            return cwd
        from aja.workspace.context import get_current_workspace
        ctx = get_current_workspace()
        return str(ctx.path if ctx else PROJECT_ROOT)

    @staticmethod
    def _result_to_dict(result) -> Dict[str, Any]:
        return {
            "status": "success" if result.success else "failed",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "code": result.exit_code,
            "session_id": result.session_id,
            "manifest_path": result.manifest_path,
        }

    async def _run_execution_async(self, command: str, cwd: str, workspace_mode: str = "direct") -> Dict[str, Any]:
        """Async-native execution path: no thread hop, never blocks the loop."""
        result = await get_default_execution_manager().run(
            ExecutionRequest(
                command=command,
                cwd=cwd,
                timeout=30,
                workspace_mode=workspace_mode,
                metadata={"legacy_api": "ToolExecutor.execute_async"},
            )
        )
        return self._result_to_dict(result)

    async def execute_async(self, command: str, cwd: str = None, workspace_mode: str = "direct") -> Dict[str, Any]:
        """Async twin of :meth:`execute` for callers already running on an event loop.

        Runs the subprocess via the ExecutionManager directly (awaited), so the
        calling loop stays responsive for the full command duration instead of
        freezing in ``thread.join()``.
        """
        logger.info(f"ToolExecutor: Executing '{command}'")

        blocked = self._check_permission(command)
        if blocked is not None:
            return blocked

        target_cwd = self._resolve_cwd(cwd)

        try:
            output = await self._run_execution_async(command, target_cwd, workspace_mode)
            self.history.append({"command": command, "result": output})
            return output
        except Exception as e:
            logger.error(f"Execution error: {e}")
            return {"status": "error", "message": str(e)}

    def execute(self, command: str, cwd: str = None, workspace_mode: str = "direct") -> Dict[str, Any]:
        """Executes a single command and returns the result."""
        logger.info(f"ToolExecutor: Executing '{command}'")

        # 1. Safety Check
        blocked = self._check_permission(command)
        if blocked is not None:
            return blocked

        # 2. Preparation
        target_cwd = self._resolve_cwd(cwd)

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
