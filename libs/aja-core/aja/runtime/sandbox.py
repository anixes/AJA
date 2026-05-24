"""
aja/runtime/sandbox.py
==========================
Hard sandbox for command execution.

Strategy:
  1. Preferred: Docker isolated container with workspace mounted.
  2. Fallback: Direct subprocess execution (if Docker daemon is unavailable).

Docker flags used when available:
  --network=none  → no outbound network access
  --memory        → RAM cap (default 256 MB)
  --cpus          → CPU cap (default 0.5 cores)
  -v HOST:WORKDIR → project workspace mounted read-write at /workspace
  -w WORKDIR      → container working directory
"""

import shutil
import asyncio
import threading
import subprocess
from typing import Optional
from aja.security.permissions import default_permissions
from aja.config import PROJECT_ROOT
from aja.runtime.execution import ExecutionRequest, get_default_execution_manager

# ---------------------------------------------------------------------------
# Docker availability detection
# ---------------------------------------------------------------------------

_DOCKER_AVAILABLE: Optional[bool] = None   # cached after first check

def _check_docker() -> bool:
    """Return True if Docker daemon is reachable."""
    global _DOCKER_AVAILABLE
    if _DOCKER_AVAILABLE is not None:
        return _DOCKER_AVAILABLE
    if not shutil.which("docker"):
        _DOCKER_AVAILABLE = False
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5
        )
        _DOCKER_AVAILABLE = (result.returncode == 0)
    except Exception:
        _DOCKER_AVAILABLE = False
    return _DOCKER_AVAILABLE


def docker_available() -> bool:
    """Public helper — True when Docker daemon is healthy."""
    return _check_docker()


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------

def is_safe(cmd: str) -> bool:
    """Check whether a command is safe to run against sandbox rules."""
    return default_permissions.validate_command(cmd)


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------

def _run_async_in_thread(coro_factory):
    result = {}
    error = {}

    def runner():
        try:
            result["value"] = asyncio.run(coro_factory())
        except Exception as exc:
            error["value"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error["value"]
    return result.get("value")


def _run_coroutine_sync(coro_factory):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    return _run_async_in_thread(coro_factory)


def _result_to_legacy(result, mode: str, warning: Optional[str] = None) -> dict:
    payload = {
        "success": result.success,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "mode": mode,
        "session_id": result.session_id,
        "manifest_path": result.manifest_path,
    }
    if warning:
        payload["warning"] = warning
    return payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_command(
    cmd: str,
    timeout: int = 60,
    memory: str = "256m",
    cpus: str = "0.5",
    workdir: str = "/workspace",
    allow_network: bool = False,
) -> dict:
    """
    Execute *cmd* safely.

    Returns a dict with keys:
      success   : bool
      stdout    : str
      stderr    : str
      exit_code : int
      mode      : 'docker' | 'direct_fallback'
      warning   : str (only present when falling back)
    """
    if not is_safe(cmd):
        raise ValueError(f"Unsafe command blocked by sandbox rules: {cmd!r}")

    use_docker = docker_available()
    warning = None if use_docker else "Docker unavailable — running in an isolated local workspace."

    async def _run():
        manager = get_default_execution_manager()
        result = await manager.run(
            ExecutionRequest(
                command=cmd,
                timeout=timeout,
                use_docker=use_docker,
                memory=memory,
                cpus=cpus,
                allow_network=allow_network,
                metadata={"legacy_api": "runtime.sandbox.execute_command"},
            )
        )
        return _result_to_legacy(result, "docker" if use_docker else "isolated_local", warning)

    return _run_coroutine_sync(_run)


async def execute_command_async(
    cmd: str,
    timeout: int = 60,
    memory: str = "256m",
    cpus: str = "0.5",
    workdir: str = "/workspace",
    allow_network: bool = False,
) -> dict:
    """Async-safe command execution wrapper for event-loop callers."""
    if not is_safe(cmd):
        raise ValueError(f"Unsafe command blocked by sandbox rules: {cmd!r}")

    use_docker = docker_available()
    result = await get_default_execution_manager().run(
        ExecutionRequest(
            command=cmd,
            timeout=timeout,
            use_docker=use_docker,
            memory=memory,
            cpus=cpus,
            allow_network=allow_network,
            metadata={"legacy_api": "runtime.sandbox.execute_command_async"},
        )
    )
    warning = None if use_docker else "Docker unavailable — running in an isolated local workspace."
    return _result_to_legacy(result, "docker" if use_docker else "isolated_local", warning)
