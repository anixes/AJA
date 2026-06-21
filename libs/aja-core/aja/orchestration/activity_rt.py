# aja/orchestration/activity_rt.py
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Literal, Optional, List

from aja.runtime.execution import ExecutionRequest, get_default_execution_manager
from aja.runtime.mission_journal import MissionJournal
from aja.security.command_guard import classify_command
from aja.config import PROJECT_ROOT


class ActivityType(str, Enum):
    PYTHON = "python"
    SHELL  = "shell"
    DOCKER = "docker"
    MCP = "mcp"
    BROWSER = "browser"
    DESKTOP = "desktop"

class RetryPolicy(str, Enum):
    NONE   = "none"
    SAFE   = "safe"
    ALWAYS = "always"

@dataclass
class Activity:
    tool:            str
    args:            Dict[str, Any]
    activity_type:   ActivityType
    trace_id:        str
    mission_id:      Optional[str] = None
    timeout_s:       int = 120
    retry_policy:    RetryPolicy = RetryPolicy.NONE
    idempotency_key: Optional[str] = None
    metadata:        Dict[str, Any] = field(default_factory=dict)

@dataclass
class ActivityResult:
    tool:        str
    success:     bool
    data:        Any
    stdout:      Optional[str] = None
    stderr:      Optional[str] = None
    exit_code:   Optional[int] = None
    env_state:   Optional[Dict] = None   # {"cwd": str, "modified_files": list}
    duration_ms: int = 0
    error:       Optional[str] = None
    session_id:  Optional[str] = None
    authorized_scope: Optional[str] = None
    permission_decision: Optional[str] = None
    grant_id: Optional[str] = None


class ActivityRuntime:
    def __init__(
        self,
        journal: Optional[MissionJournal] = None,
        exec_manager=None,
        dry_run: bool = False,
        permission_engine=None,
        mcp_manager=None,
        browser_backend=None,
        desktop_backend=None,
    ):
        self._journal = journal
        self._exec_manager = exec_manager or get_default_execution_manager()
        self._shell_cwd: str = str(PROJECT_ROOT)   # Gap 1: persistent CWD
        self.dry_run = dry_run
        self._permission_engine = permission_engine
        self._mcp_manager = mcp_manager
        self._browser_backend = browser_backend
        self._desktop_backend = desktop_backend

    async def run(self, activity: Activity) -> ActivityResult:
        authorization = self._authorize(activity)
        if not authorization.allowed:
            result = ActivityResult(
                tool=activity.tool,
                success=False,
                data=None,
                error=f"Permission denied for scope {authorization.scope}: {authorization.reason}",
                authorized_scope=authorization.scope,
                permission_decision=authorization.decision,
                grant_id=authorization.grant_id,
            )
            if self._journal:
                self._journal.emit("TOOL_FAILED", {
                    "tool": activity.tool,
                    "error": result.error,
                    "trace_id": activity.trace_id,
                    "authorized_scope": authorization.scope,
                    "permission_decision": authorization.decision,
                    "grant_id": authorization.grant_id,
                })
            return result

        # Journal: activity started
        if self._journal:
            self._journal.emit("TOOL_CALLED", {
                "tool": activity.tool,
                "args": activity.args,
                "activity_type": activity.activity_type.value,
                "trace_id": activity.trace_id,
                "authorized_scope": authorization.scope,
                "permission_decision": authorization.decision,
                "grant_id": authorization.grant_id,
            })

        try:
            if activity.activity_type == ActivityType.PYTHON:
                result = await self._run_python(activity)
            elif activity.activity_type in (ActivityType.SHELL, ActivityType.DOCKER):
                result = await self._run_shell(activity)
            elif activity.activity_type == ActivityType.MCP:
                result = await self._run_mcp(activity)
            elif activity.activity_type == ActivityType.BROWSER:
                result = await self._run_browser(activity)
            elif activity.activity_type == ActivityType.DESKTOP:
                result = await self._run_desktop(activity)
            else:
                raise ValueError(f"Unknown activity_type: {activity.activity_type}")

            result.authorized_scope = authorization.scope
            result.permission_decision = authorization.decision
            result.grant_id = authorization.grant_id

            # Journal: activity completed
            if self._journal:
                self._journal.emit("TOOL_COMPLETED", {
                    "tool": activity.tool,
                    "success": result.success,
                    "exit_code": result.exit_code,
                    "duration_ms": result.duration_ms,
                    "env_state": result.env_state,
                    "trace_id": activity.trace_id,
                    "authorized_scope": authorization.scope,
                    "permission_decision": authorization.decision,
                    "grant_id": authorization.grant_id,
                })
            return result

        except Exception as e:
            if self._journal:
                self._journal.emit("TOOL_FAILED", {
                    "tool": activity.tool,
                    "error": str(e),
                    "trace_id": activity.trace_id,
                    "authorized_scope": authorization.scope,
                    "permission_decision": authorization.decision,
                    "grant_id": authorization.grant_id,
                })
            raise

    def _authorize(self, activity: Activity):
        from aja.security.command_guard import classify_command
        from aja.security.permissions import PermissionEngine, required_scope_for_shell

        engine = self._permission_engine or PermissionEngine()
        scope = activity.metadata.get("required_scope")
        reason = activity.metadata.get("permission_reason", "")
        if not scope:
            if activity.activity_type == ActivityType.SHELL:
                classification = classify_command(activity.args.get("cmd", ""))
                scope = required_scope_for_shell(activity.args.get("cmd", ""), classification)
                reason = "; ".join(classification.get("reasons", []))
            elif activity.activity_type == ActivityType.PYTHON:
                scope = f"python.{activity.tool}"
            elif activity.activity_type == ActivityType.MCP:
                server_id = activity.metadata.get("server_id", "unknown")
                mcp_tool = activity.metadata.get("mcp_tool", activity.tool.rsplit(".", 1)[-1])
                scope = f"mcp.{server_id}.{mcp_tool}"
            elif activity.activity_type == ActivityType.BROWSER:
                scope = "browser.read" if activity.tool in {"browser.extract_text", "browser.screenshot"} else "browser.navigate" if activity.tool == "browser.navigate" else "browser.interact"
            elif activity.activity_type == ActivityType.DESKTOP:
                scope = "desktop.interact"
            else:
                scope = activity.activity_type.value
        return engine.authorize(
            scope,
            mission_id=activity.mission_id,
            journal=self._journal,
            dry_run=self.dry_run,
            reason=reason,
        )

    async def _run_shell(self, activity: Activity) -> ActivityResult:
        import os
        # Gap 2: CommandGuard on args.cmd (not raw LLM text)
        cmd = activity.args.get("cmd", "")
        classification = classify_command(cmd)
        if classification["decision"] == "deny":
            return ActivityResult(
                tool=activity.tool,
                success=False,
                data=None,
                error="CommandGuard blocked: " + "; ".join(classification["reasons"])
            )

        # Gap 1: Update persisted CWD after 'cd' commands
        if cmd.strip().startswith("cd "):
            new_dir = cmd.strip()[3:].strip().strip("\"'")
            candidate = Path(new_dir) if Path(new_dir).is_absolute() else Path(self._shell_cwd) / new_dir
            if candidate.exists() or self.dry_run:
                self._shell_cwd = str(Path(os.path.normpath(candidate)).resolve())

        if self.dry_run:
            return ActivityResult(
                tool=activity.tool,
                success=True,
                data=f"[DRY-RUN SIMULATION OUTPUT] Successfully simulated command: {cmd}",
                stdout=f"[DRY-RUN SIMULATION OUTPUT] Successfully simulated command: {cmd}",
                stderr="",
                exit_code=0,
                env_state={
                    "cwd": self._shell_cwd,
                    "exit_code": 0,
                    "modified_files": [],
                },
                duration_ms=5,
            )

        req = ExecutionRequest(
            command=cmd,
            cwd=self._shell_cwd,          # Gap 1: use persisted CWD
            timeout=float(activity.timeout_s),
            workspace_mode="direct",
            metadata={"tool": activity.tool, "trace_id": activity.trace_id},
        )
        exec_result = await self._exec_manager.run(req)

        # Gap 3: Build env_state from WorkspaceDiff
        env_state = {
            "cwd": self._shell_cwd,
            "exit_code": exec_result.exit_code,
            "modified_files": exec_result.workspace_diff.untracked_files if exec_result.workspace_diff else [],
        }

        return ActivityResult(
            tool=activity.tool,
            success=exec_result.success,
            data=exec_result.stdout,
            stdout=exec_result.stdout,
            stderr=exec_result.stderr,
            exit_code=exec_result.exit_code,
            env_state=env_state,            # Gap 3
            duration_ms=exec_result.duration_ms,
            error=exec_result.error,
            session_id=exec_result.session_id,
        )

    async def _run_python(self, activity: Activity) -> ActivityResult:
        from aja.orchestration.tools.native import NativeToolRegistry
        registry = NativeToolRegistry()
        t0 = time.monotonic()
        result_str = registry.execute(activity.tool, activity.args)
        duration_ms = int((time.monotonic() - t0) * 1000)
        is_security_err = result_str.startswith("Security Error")
        is_err = result_str.startswith("Error") or is_security_err
        return ActivityResult(
            tool=activity.tool,
            success=not is_err,
            data=result_str if not is_err else None,
            duration_ms=duration_ms,
            error=result_str if is_err else None,
        )

    async def _run_mcp(self, activity: Activity) -> ActivityResult:
        t0 = time.monotonic()
        server_id = activity.metadata.get("server_id")
        mcp_tool = activity.metadata.get("mcp_tool")
        if not server_id or not mcp_tool:
            parts = activity.tool.split(".")
            if len(parts) >= 3:
                server_id = server_id or parts[1]
                mcp_tool = mcp_tool or ".".join(parts[2:])
        if self.dry_run:
            return ActivityResult(
                tool=activity.tool,
                success=True,
                data={"dry_run": True, "backend": "mcp", "server_id": server_id, "tool": mcp_tool, "args": activity.args},
                duration_ms=5,
            )
        manager = self._mcp_manager
        if manager is None:
            from aja.api.mcp_client import get_default_mcp_manager
            manager = get_default_mcp_manager()
        data = await manager.call_tool(server_id, mcp_tool, activity.args)
        return ActivityResult(
            tool=activity.tool,
            success=True,
            data=data,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    async def _run_browser(self, activity: Activity) -> ActivityResult:
        t0 = time.monotonic()
        mission_id = activity.mission_id or activity.trace_id
        backend = self._browser_backend
        if backend is None:
            from aja.backends.browser import get_default_browser_backend
            backend = get_default_browser_backend()
        data = await (backend.dry_run(mission_id, activity.tool, activity.args) if self.dry_run else backend.execute(mission_id, activity.tool, activity.args))
        return ActivityResult(
            tool=activity.tool,
            success=True,
            data=data,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    async def _run_desktop(self, activity: Activity) -> ActivityResult:
        t0 = time.monotonic()
        backend = self._desktop_backend
        if backend is None:
            from aja.backends.desktop import get_default_desktop_backend
            backend = get_default_desktop_backend()
        data = await (backend.dry_run(activity.tool, activity.args) if self.dry_run else backend.execute(activity.tool, activity.args))
        return ActivityResult(
            tool=activity.tool,
            success=True,
            data=data,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
