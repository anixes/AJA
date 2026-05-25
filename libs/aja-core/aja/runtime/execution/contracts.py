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
        workspace: Optional[WorkspaceSnapshot],
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
            metadata=dict(request.metadata),
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
