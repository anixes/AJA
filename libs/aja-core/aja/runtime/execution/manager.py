from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from aja.config import PROJECT_ROOT
from aja.observability.telemetry import get_trace_id
from aja.runtime.event_bus import EVENTS, bus
from aja.runtime.events import NullRuntimeEventSink, RuntimeEventSink
from aja.runtime.execution.contracts import (
    ExecutionManifest,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSession,
    ExecutionState,
    ExecutionStreamEvent,
    utc_now,
)
from aja.runtime.execution.governance import GovernancePolicy, create_posix_preexec_fn
from aja.runtime.execution.supervisor import snapshot_process, terminate_tree
from aja.runtime.execution.workspace import WorkspaceManager


class ExecutionManager:
    """Canonical async execution runtime with streaming and cleanup semantics."""

    def __init__(
        self,
        project_root: Optional[Path] = None,
        event_sink: Optional[RuntimeEventSink] = None,
        workspace_manager: Optional[WorkspaceManager] = None,
        governance_policy: Optional[GovernancePolicy] = None,
    ):
        self.project_root = (project_root or PROJECT_ROOT).resolve()
        self.base_dir = self.project_root / ".aja" / "executions"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.event_sink = event_sink or NullRuntimeEventSink()
        self.workspace_manager = workspace_manager or WorkspaceManager(self.project_root)
        self.governance = governance_policy or GovernancePolicy()
        self._sessions: Dict[str, ExecutionSession] = {}
        self._lock = asyncio.Lock()

    async def start(self, request: ExecutionRequest) -> ExecutionSession:
        session_id = request.metadata.get("session_id") or f"exec-{uuid.uuid4().hex[:12]}"
        root = self.base_dir / session_id
        root.mkdir(parents=True, exist_ok=True)
        session = ExecutionSession(
            session_id=session_id,
            request=request,
            state="created",
            trace_id=get_trace_id(),
            run_id=request.metadata.get("run_id"),
            root=root,
            manifest_path=root / "manifest.json",
            stdout_path=root / "stdout.log",
            stderr_path=root / "stderr.log",
        )
        async with self._lock:
            self._sessions[session_id] = session
        session.task = asyncio.create_task(self._run_session(session), name=f"aja-execution-{session_id}")
        return session

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        session = await self.start(request)
        return await self.wait(session.session_id)

    async def wait(self, session_id: str) -> ExecutionResult:
        session = self._sessions[session_id]
        if session.task:
            try:
                await session.task
            except asyncio.CancelledError:
                await self.cancel(session_id)
        if session.result is None:
            raise RuntimeError(f"Execution session {session_id} did not produce a result")
        return session.result

    async def cancel(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if not session or session.state in {"completed", "failed", "cancelled", "timeout"}:
            return
        await self._shutdown_process(session, "cancelled")

    async def send_stdin(self, session_id: str, data: str) -> bool:
        session = self._sessions.get(session_id)
        proc = session.process if session else None
        if not proc or not proc.stdin:
            return False
        proc.stdin.write(data.encode())
        await proc.stdin.drain()
        return True

    def list_active(self) -> List[Dict[str, Any]]:
        return [
            session.snapshot()
            for session in self._sessions.values()
            if session.state in {"created", "starting", "running", "graceful_shutdown", "force_kill"}
        ]

    def get_timeline(self, session_id: str) -> List[Dict[str, Any]]:
        session = self._sessions.get(session_id)
        if session:
            return list(session.timeline)
        timeline_path = self.base_dir / session_id / "timeline.jsonl"
        if not timeline_path.exists():
            return []
        events = []
        for line in timeline_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except Exception:
                continue
        return events

    def get_diff(self, session_id: str) -> Dict[str, Any]:
        path = self.base_dir / session_id / "workspace_diff.json"
        if not path.exists():
            return {"session_id": session_id, "diff_text": "", "untracked_files": [], "artifact_files": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def cleanup_stale(self) -> List[str]:
        return self.workspace_manager.cleanup_stale()

    async def _run_session(self, session: ExecutionSession) -> None:
        started = utc_now()
        session.started_at = started
        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []
        final_state: ExecutionState = "failed"
        exit_code = -1
        error: Optional[str] = None

        # Apply Governance Policy Limits
        limits = self.governance.apply(session.request)
        backend = "docker" if limits.use_docker else self._backend_name()

        try:
            await self._set_state(session, "starting", "Execution session starting")
            workspace = self.workspace_manager.create(session.session_id, session.request.workspace_mode)
            session.workspace = workspace
            cwd = self._resolve_cwd(session.request.cwd, workspace)
            
            manifest = ExecutionManifest.create(
                session_id=session.session_id,
                request=session.request,
                trace_id=session.trace_id,
                run_id=session.run_id,
                cwd=cwd,
                backend=backend,
                workspace=workspace,
                applied_limits=limits.to_dict(),
            )
            self._write_json(session.manifest_path, manifest.to_dict())
            await self._emit(session, "EXECUTION_WORKSPACE_CREATED", "Workspace prepared", {"workspace": workspace.to_dict()})
            await self._set_state(session, "running", "Execution session running")

            command, shell = self._command_for_request(session.request, workspace, limits)
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            
            # Apply POSIX resource limits if executing locally without Docker
            preexec_fn = create_posix_preexec_fn(limits) if not limits.use_docker else (None if os.name == "nt" else os.setsid)
            
            env = os.environ.copy()
            env.update(session.request.env)
            env["AJA_EXECUTION_SESSION_ID"] = session.session_id
            env["AJA_TRACE_ID"] = session.trace_id or ""

            proc = None
            if getattr(session.request, "use_pty", False) and os.name == "nt":
                try:
                    from aja.runtime.execution.pty_windows import WindowsPTYProcess, pywinpty
                    if pywinpty is not None:
                        cmd_str = command if isinstance(command, str) else " ".join(shlex.quote(str(p)) for p in command)
                        proc = WindowsPTYProcess(cmd=cmd_str, cwd=str(cwd), env=env)
                        proc.start()
                except Exception as e:
                    pass  # Graceful fallback to subprocess

            if proc is None:
                if shell:
                    proc = await asyncio.create_subprocess_shell(
                        command if isinstance(command, str) else " ".join(shlex.quote(str(p)) for p in command),
                        cwd=str(cwd),
                        env=env,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        creationflags=creationflags,
                        preexec_fn=preexec_fn,
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=str(cwd),
                        env=env,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        creationflags=creationflags,
                        preexec_fn=preexec_fn,
                    )

            session.process = proc
            session.pid = proc.pid
            await self._emit(session, "EXECUTION_PROCESS_STARTED", "Process started", {"pid": proc.pid})

            if session.request.stdin and proc.stdin:
                try:
                    proc.stdin.write(session.request.stdin.encode())
                    await proc.stdin.drain()
                    proc.stdin.close()
                except Exception:
                    pass

            readers = []
            if proc.stdout:
                readers.append(asyncio.create_task(self._read_stream(session, "stdout", proc.stdout, stdout_chunks)))
            if proc.stderr:
                readers.append(asyncio.create_task(self._read_stream(session, "stderr", proc.stderr, stderr_chunks)))
            try:
                await asyncio.wait_for(proc.wait(), timeout=limits.timeout)
                if session.state in {"cancelled", "timeout", "force_kill", "graceful_shutdown"}:
                    final_state = "cancelled" if session.state in {"cancelled", "force_kill", "graceful_shutdown"} else "timeout"
                    exit_code = -1
                    error = error or f"Execution {final_state}."
                else:
                    exit_code = int(proc.returncode or 0)
                    final_state = "completed" if exit_code == 0 else "failed"
            except asyncio.TimeoutError:
                error = f"Command timed out after {limits.timeout}s."
                await self._shutdown_process(session, "timeout")
                exit_code = -1
                final_state = "timeout"
            except asyncio.CancelledError:
                error = "Execution cancelled."
                await self._shutdown_process(session, "cancelled")
                exit_code = -1
                final_state = "cancelled"
                raise
            finally:
                await asyncio.gather(*readers, return_exceptions=True)

            session.returncode = exit_code
            manifest.process = snapshot_process(session.pid, exit_code)
            self._write_json(session.manifest_path, manifest.to_dict())
            await self._emit(
                session,
                "EXECUTION_PROCESS_EXITED",
                "Process exited",
                {"pid": session.pid, "exit_code": exit_code, "state": final_state},
            )

        except asyncio.CancelledError:
            final_state = "cancelled"
            error = error or "Execution cancelled."
            exit_code = -1
        except Exception as exc:
            final_state = "failed"
            error = str(exc)
            await self._emit(session, "EXECUTION_ERROR", error, {"error": error}, level="error")
        finally:
            workspace_diff = None
            if session.workspace:
                try:
                    workspace_diff = self.workspace_manager.diff(session.workspace)
                    self._write_json(session.root / "workspace_diff.json", workspace_diff.to_dict())
                    await self._emit(
                        session,
                        "EXECUTION_WORKSPACE_DIFF",
                        "Workspace diff captured",
                        {
                            "untracked_count": len(workspace_diff.untracked_files),
                            "artifact_count": len(workspace_diff.artifact_files),
                            "diff_bytes": len(workspace_diff.diff_text.encode("utf-8")),
                        },
                    )
                except Exception as exc:
                    await self._emit(session, "EXECUTION_WORKSPACE_DIFF_FAILED", str(exc), level="warning")
                try:
                    self.workspace_manager.cleanup(session.workspace)
                    await self._emit(session, "EXECUTION_WORKSPACE_CLEANED", "Workspace cleaned")
                except Exception as exc:
                    final_state = "cleanup_failed" if final_state == "completed" else final_state
                    await self._emit(session, "EXECUTION_WORKSPACE_CLEANUP_FAILED", str(exc), level="error")

            ended = utc_now()
            session.ended_at = ended
            duration_ms = self._duration_ms(session.started_at or started, ended)
            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)
            if error and final_state in {"timeout", "cancelled"} and not stderr:
                stderr = error
                self._append_text(session.stderr_path, stderr + "\n")

            success = final_state == "completed" and exit_code == 0
            session.state = final_state
            session.result = ExecutionResult(
                session_id=session.session_id,
                success=success,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                state=final_state,
                started_at=session.started_at or started,
                ended_at=ended,
                duration_ms=duration_ms,
                mode=backend,
                manifest_path=str(session.manifest_path),
                workspace_diff=workspace_diff,
                error=error,
            )
            self._write_json(session.root / "result.json", session.result.to_dict())
            await self._emit(
                session,
                "EXECUTION_SESSION_FINISHED",
                f"Execution {final_state}",
                {"exit_code": exit_code, "duration_ms": duration_ms, "state": final_state},
                level="error" if not success else "info",
                status="success" if success else "failed",
            )

    async def _read_stream(self, session: ExecutionSession, stream: str, reader: Any, chunks: List[str]) -> None:
        if reader is None:
            return
        path = session.stdout_path if stream == "stdout" else session.stderr_path
        sequence = 0
        while True:
            raw = await reader.readline()
            if not raw:
                break
            text = raw.decode("utf-8", errors="replace")
            chunks.append(text)
            self._append_text(path, text)
            event = ExecutionStreamEvent(session.session_id, stream, text.rstrip("\r\n"), sequence, trace_id=session.trace_id)
            sequence += 1
            await self._record_timeline(session, f"EXECUTION_{stream.upper()}_LINE", event.to_dict())
            await self._emit(
                session,
                f"EXECUTION_{stream.upper()}",
                event.line,
                {"stream": stream, "line": event.line, "sequence": event.sequence},
            )

    async def _shutdown_process(self, session: ExecutionSession, reason: ExecutionState) -> None:
        await self._set_state(session, "graceful_shutdown", f"Execution {reason}: graceful shutdown")
        terminate_tree(session.pid, force=False)
        try:
            if session.process:
                await asyncio.wait_for(session.process.wait(), timeout=2.0)
                session.state = reason
                return
        except Exception:
            pass
        await self._set_state(session, "force_kill", f"Execution {reason}: force cleanup")
        terminate_tree(session.pid, force=True)
        if session.process:
            try:
                await asyncio.wait_for(session.process.wait(), timeout=2.0)
            except Exception:
                pass
        session.state = reason

    async def _set_state(self, session: ExecutionSession, state: ExecutionState, message: str) -> None:
        session.state = state
        await self._emit(session, "EXECUTION_STATE_CHANGED", message, {"state": state})

    async def _emit(
        self,
        session: ExecutionSession,
        event_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        level: str = "info",
        status: str = "success",
    ) -> None:
        payload = {
            "event_type": event_type,
            "tool": "execution",
            "message": message,
            "level": level,
            "status": status,
            "trace_id": session.trace_id,
            "run_id": session.run_id,
            "metadata": {"session_id": session.session_id, **(metadata or {})},
        }
        await self._record_timeline(session, event_type, payload)
        try:
            self.event_sink.emit(payload)
        except Exception:
            pass
        try:
            bus.publish(event_type, payload)
        except Exception:
            pass

    async def _record_timeline(self, session: ExecutionSession, event_type: str, payload: Dict[str, Any]) -> None:
        event = {"timestamp": utc_now(), "event_type": event_type, **payload}
        session.timeline.append(event)
        self._append_text(session.root / "timeline.jsonl", json.dumps(event, default=str) + "\n")

    def _command_for_request(self, request: ExecutionRequest, workspace: Any, limits: Any) -> tuple[Any, bool]:
        if not limits.use_docker:
            return request.command, request.shell
        network = "bridge" if limits.allow_network else "none"
        docker_cmd = [
            "docker",
            "run",
            "--rm",
            f"--network={network}",
            f"--memory={limits.memory_str}",
            f"--cpus={limits.cpus}",
            "-v",
            f"{workspace.execution_root}:/workspace",
            "-w",
            "/workspace",
            request.docker_image,
            "bash",
            "-c",
            request.command,
        ]
        return docker_cmd, False

    def _resolve_cwd(self, requested_cwd: Optional[str], workspace: Any) -> str:
        if not requested_cwd:
            return workspace.execution_root
        requested = Path(requested_cwd).resolve()
        if workspace.mode != "direct":
            try:
                relative = requested.relative_to(self.project_root)
                mapped = Path(workspace.execution_root) / relative
                if mapped.exists():
                    return str(mapped)
            except Exception:
                pass
            return workspace.execution_root
        return str(requested)

    def _backend_name(self) -> str:
        if os.name == "posix":
            return "posix_pty_stream"
        return "windows_async_subprocess_stream"

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def _append_text(self, path: Optional[Path], text: str) -> None:
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(text)

    def _duration_ms(self, start: str, end: str) -> int:
        from datetime import datetime

        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        return int((e - s).total_seconds() * 1000)


_DEFAULT_MANAGER: Optional[ExecutionManager] = None


def get_default_execution_manager() -> ExecutionManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = ExecutionManager()
    return _DEFAULT_MANAGER


for event_name in [
    "EXECUTION_STATE_CHANGED",
    "EXECUTION_WORKSPACE_CREATED",
    "EXECUTION_PROCESS_STARTED",
    "EXECUTION_STDOUT",
    "EXECUTION_STDERR",
    "EXECUTION_PROCESS_EXITED",
    "EXECUTION_WORKSPACE_DIFF",
    "EXECUTION_WORKSPACE_CLEANED",
    "EXECUTION_SESSION_FINISHED",
]:
    EVENTS.setdefault(event_name, event_name)
