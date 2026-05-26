from __future__ import annotations

import os
import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


ExecutionState = Literal[
    "created",
    "starting",
    "running",
    "graceful_shutdown",
    "force_kill",
    "completed",
    "failed",
    "cancelled",
    "timeout",
    "cleanup_failed",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExecutionRequest:
    command: str
    timeout: float = 60.0
    cwd: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    shell: bool = True
    allow_network: bool = False
    use_docker: bool = False
    docker_image: str = "python:3.10-slim"
    memory: str = "256m"
    cpus: str = "0.5"
    workspace_mode: Literal["isolated", "direct"] = "isolated"
    stdin: Optional[str] = None
    use_pty: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Full lossless serialization — every field included for journal persistence."""
        return {
            "command": self.command,
            "timeout": self.timeout,
            "cwd": self.cwd,
            "env": dict(self.env),
            "shell": self.shell,
            "allow_network": self.allow_network,
            "use_docker": self.use_docker,
            "docker_image": self.docker_image,
            "memory": self.memory,
            "cpus": self.cpus,
            "workspace_mode": self.workspace_mode,
            "stdin": self.stdin,
            "use_pty": self.use_pty,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionRequest":
        """Reconstruct a fully-typed ExecutionRequest from a persisted dict.

        Tolerant of missing keys so pre-Phase-1 manifests still deserialize
        without raising (missing fields get their dataclass defaults).
        """
        return cls(
            command=data.get("command", ""),
            timeout=float(data.get("timeout", 60.0)),
            cwd=data.get("cwd"),
            env=dict(data.get("env") or {}),
            shell=bool(data.get("shell", True)),
            allow_network=bool(data.get("allow_network", False)),
            use_docker=bool(data.get("use_docker", False)),
            docker_image=str(data.get("docker_image", "python:3.10-slim")),
            memory=str(data.get("memory", "256m")),
            cpus=str(data.get("cpus", "0.5")),
            workspace_mode=data.get("workspace_mode", "isolated"),
            stdin=data.get("stdin"),
            use_pty=bool(data.get("use_pty", False)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class ProcessSnapshot:
    pid: Optional[int] = None
    returncode: Optional[int] = None
    children: List[int] = field(default_factory=list)
    cpu_percent: Optional[float] = None
    memory_rss: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkspaceSnapshot:
    session_id: str
    source_root: str
    execution_root: str
    artifact_root: str
    mode: str
    created_at: str = field(default_factory=utc_now)
    cleanup_required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkspaceDiff:
    session_id: str
    schema_version: str = "1.0"
    diff_text: str = ""
    untracked_files: List[str] = field(default_factory=list)
    artifact_files: List[str] = field(default_factory=list)
    generated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionStreamEvent:
    session_id: str
    stream: Literal["stdout", "stderr", "lifecycle"]
    line: str
    sequence: int
    timestamp: str = field(default_factory=utc_now)
    trace_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionManifest:
    session_id: str
    command: str
    trace_id: Optional[str]
    run_id: Optional[str]
    created_at: str
    cwd: str
    backend: str
    schema_version: str = "1.0"
    workspace: Optional[WorkspaceSnapshot] = None
    environment: Dict[str, Any] = field(default_factory=dict)
    applied_limits: Dict[str, Any] = field(default_factory=dict)
    process: ProcessSnapshot = field(default_factory=ProcessSnapshot)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        session_id: str,
        request: ExecutionRequest,
        trace_id: Optional[str],
        run_id: Optional[str],
        cwd: str,
        backend: str,
        workspace: Optional["WorkspaceSnapshot"],
        applied_limits: Optional[Dict[str, Any]] = None,
    ) -> "ExecutionManifest":
        return cls(
            session_id=session_id,
            command=request.command,
            trace_id=trace_id,
            run_id=run_id,
            created_at=utc_now(),
            cwd=cwd,
            backend=backend,
            schema_version="1.0",
            workspace=workspace,
            applied_limits=applied_limits or {},
            environment={
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "pid": os.getpid(),
                "executable": sys.executable,
            },
            # Phase 1: Persist full ExecutionRequest so rehydration is lossless.
            # The 'request' key is separate from 'metadata' to avoid collisions.
            metadata={"request": request.to_dict(), **dict(request.metadata)},
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.workspace:
            data["workspace"] = self.workspace.to_dict()
        return data


@dataclass
class ExecutionResult:
    session_id: str
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    state: ExecutionState
    started_at: str
    ended_at: str
    duration_ms: int
    mode: str
    manifest_path: str
    schema_version: str = "1.0"
    workspace_diff: Optional[WorkspaceDiff] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.workspace_diff:
            data["workspace_diff"] = self.workspace_diff.to_dict()
        return data


class StateTransitionError(RuntimeError):
    """Raised when an invalid state transition is attempted inside ExecutionSession."""
    pass


ALLOWED_TRANSITIONS: Dict[ExecutionState, set[ExecutionState]] = {
    "created": {"starting"},
    "starting": {"running", "failed", "cancelled"},
    "running": {"graceful_shutdown", "completed", "failed", "cancelled", "timeout"},
    "graceful_shutdown": {"force_kill", "cancelled", "timeout"},
    "force_kill": {"cancelled", "timeout"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
    "timeout": set(),
    "cleanup_failed": set(),
}


@dataclass
class ExecutionSession:
    session_id: str
    request: ExecutionRequest
    state: ExecutionState
    trace_id: Optional[str]
    run_id: Optional[str]
    root: Path
    manifest_path: Path
    schema_version: str = "1.0"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    pid: Optional[int] = None
    returncode: Optional[int] = None
    stdout_path: Optional[Path] = None
    stderr_path: Optional[Path] = None
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    task: Any = None
    process: Any = None
    workspace: Optional[WorkspaceSnapshot] = None
    result: Optional[ExecutionResult] = None

    def transition_to(self, target: ExecutionState, emitter: Optional[Any] = None) -> None:
        """Enforces FSM constraints on state modifications and journals the transition event."""
        current = self.state
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if target not in allowed and target != current:
            raise StateTransitionError(
                f"Invalid FSM state transition: Cannot shift ExecutionSession from '{current}' to '{target}'."
            )
        
        self.state = target
        if emitter is not None:
            emitter.emit(
                "EXECUTION_STATE_CHANGED",
                {
                    "event_type": "execution.transition",
                    "from": current,
                    "to": target,
                    "state": target,
                    "message": f"Execution shifted from {current} to {target}",
                },
            )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "pid": self.pid,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "command": self.request.command,
            "root": str(self.root),
            "workspace": self.workspace.to_dict() if self.workspace else None,
        }
