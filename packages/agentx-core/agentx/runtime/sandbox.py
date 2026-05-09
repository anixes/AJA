"""
agentx/runtime/sandbox.py
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

import subprocess
import shutil
from typing import Optional
from agentx.security.permissions import default_permissions
from agentx.config import PROJECT_ROOT

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

def _run_in_docker(cmd: str, timeout: int, memory: str, cpus: str, workdir: str) -> dict:
    """Execute *cmd* inside an isolated Docker container."""
    host_path = str(PROJECT_ROOT.resolve())

    docker_cmd = [
        "docker", "run", "--rm",
        "--network=none",
        f"--memory={memory}",
        f"--cpus={cpus}",
        "-v", f"{host_path}:{workdir}",
        "-w", workdir,
        "python:3.10-slim",
        "bash", "-c", cmd,
    ]
    try:
        result = subprocess.run(
            docker_cmd, capture_output=True, text=True, timeout=timeout
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "mode": "docker",
        }
    except subprocess.TimeoutExpired as e:
        return {
            "success": False,
            "stdout": e.stdout.decode() if e.stdout else "",
            "stderr": f"Docker command timed out after {timeout}s.",
            "exit_code": -1,
            "mode": "docker",
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Docker execution error: {e}",
            "exit_code": -1,
            "mode": "docker",
        }


def _run_direct(cmd: str, timeout: int) -> dict:
    """Fallback: execute *cmd* directly via subprocess (no container isolation)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(PROJECT_ROOT)
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "mode": "direct_fallback",
            "warning": "Docker unavailable — running without container isolation.",
        }
    except subprocess.TimeoutExpired as e:
        return {
            "success": False,
            "stdout": e.stdout.decode() if e.stdout else "",
            "stderr": f"Command timed out after {timeout}s.",
            "exit_code": -1,
            "mode": "direct_fallback",
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Subprocess error: {e}",
            "exit_code": -1,
            "mode": "direct_fallback",
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_command(
    cmd: str,
    timeout: int = 60,
    memory: str = "256m",
    cpus: str = "0.5",
    workdir: str = "/workspace",
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

    if docker_available():
        return _run_in_docker(cmd, timeout, memory, cpus, workdir)
    else:
        return _run_direct(cmd, timeout)
