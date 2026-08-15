"""
AJA Native System Tools: Host Inspection and SysAdmin Diagnostics
Provides tools for querying system resources, service health, and container state.
"""

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_system_specs() -> Dict[str, Any]:
    """Retrieves CPU, RAM, OS, and disk specifications of the host."""
    total, used, free = shutil.disk_usage(Path.home())
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count() or 1,
        "disk_total_gb": round(total / (1024 ** 3), 2),
        "disk_used_gb": round(used / (1024 ** 3), 2),
        "disk_free_gb": round(free / (1024 ** 3), 2),
        "user": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "home": str(Path.home()),
    }


def get_disk_usage(path: Optional[str] = None) -> Dict[str, Any]:
    """Returns disk usage metrics for a given path or root."""
    target = Path(path or Path.home()).resolve()
    try:
        total, used, free = shutil.disk_usage(target)
        return {
            "path": str(target),
            "total_gb": round(total / (1024 ** 3), 2),
            "used_gb": round(used / (1024 ** 3), 2),
            "free_gb": round(free / (1024 ** 3), 2),
            "percent_used": round((used / total) * 100, 1),
        }
    except Exception as e:
        return {"error": str(e)}


def inspect_docker_containers() -> List[Dict[str, str]]:
    """Lists running and stopped Docker containers on the host."""
    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.ID}}|{{.Image}}|{{.Status}}|{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return [{"status": "docker_unavailable", "detail": proc.stderr.strip()}]

        containers = []
        for line in proc.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 4:
                containers.append({
                    "id": parts[0],
                    "image": parts[1],
                    "status": parts[2],
                    "name": parts[3],
                })
        return containers
    except Exception as e:
        return [{"status": "docker_not_running", "error": str(e)}]


def get_service_status(service_name: str) -> Dict[str, Any]:
    """Queries systemd service status on Linux or sc query on Windows."""
    is_windows = os.name == "nt"
    if is_windows:
        cmd = ["sc", "query", service_name]
    else:
        cmd = ["systemctl", "status", service_name]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return {
            "service": service_name,
            "status": "running" if proc.returncode == 0 else "inactive_or_failed",
            "output": proc.stdout[:1000] or proc.stderr[:1000],
        }
    except Exception as e:
        return {
            "service": service_name,
            "status": "unknown",
            "error": str(e),
        }


def get_active_ports() -> List[str]:
    """Lists active listening network sockets."""
    is_windows = os.name == "nt"
    cmd = ["netstat", "-ano"] if is_windows else ["ss", "-tulpn"]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines = [line.strip() for line in proc.stdout.splitlines() if "LISTEN" in line or "LISTENING" in line]
        return lines[:20]  # Return top 20 listening sockets
    except Exception as e:
        return [f"Error querying sockets: {e}"]
