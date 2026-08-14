"""
AJA CLI Command: status
======================
Real-time status, hardware diagnostics, and log tailing.
"""

import json
import os
import subprocess
from aja.config import CONFIG_PATH, DATA_DIR, PROJECT_ROOT
from aja.interface.modern import console, print_status


def cmd_status(agent_mode: bool = False):
    """Real-time overview of swarm health and active batons."""
    from aja.memory.manager import get_memory_manager

    mgr = get_memory_manager()

    # Mode Check
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
            mode = cfg.get("swarm_settings", {}).get("operating_mode", "OFFLINE")
    except Exception:
        mode = "UNKNOWN"

    # Active Batons
    batons = []
    baton_dir = DATA_DIR / "batons"
    if baton_dir.exists():
        for b in baton_dir.glob("*.json"):
            try:
                with open(b, "r") as f:
                    data = json.load(f)
                    batons.append(
                        {
                            "id": b.stem,
                            "objective": data.get("objective", "Unknown"),
                            "updated_at": data.get("updated_at", "-"),
                        }
                    )
            except Exception as e:
                print(f"[!] Error reading state: {e}")

    # Recent Tasks from Arrow
    tasks = []
    try:
        from aja.persistence.tasks import fetch_pending_tasks

        tasks = fetch_pending_tasks(limit=5)
    except Exception:
        pass

    if agent_mode:
        output = {
            "mode": mode,
            "batons": batons,
            "tasks": [
                {
                    "id": str(t.get("id", "")),
                    "status": t.get("status", ""),
                    "input": t.get("input", ""),
                    "updated_at": t.get("updated_at", "-"),
                }
                for t in tasks
            ],
        }
        print(json.dumps(output, indent=2), flush=True)
        return

    print_status(mode, batons, tasks)


def run_gpu_check():
    """
    Check active GPU diagnostics using nvidia-smi, falling back to CPU/RAM/Disk resources.
    """
    console.print("\n Telemetry & Hardware Diagnostics")
    try:
        res = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            console.print("[green]Active GPU Diagnostics (nvidia-smi):[/]")
            console.print(res.stdout)
            return
    except Exception:
        pass

    console.print(
        "[yellow]⚠ Specialized GPU diagnostics (nvidia-smi) unavailable or not found.[/]"
    )
    console.print("[bold cyan]System Resources Fallback Diagnostics:[/]")
    try:
        import psutil
    except ImportError:
        psutil = None

    if psutil is not None:
        try:
            cpu_count = psutil.cpu_count(logical=True)
            cpu_percent = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            total_ram_gb = ram.total / (1024**3)
            used_ram_gb = ram.used / (1024**3)
            free_ram_gb = ram.available / (1024**3)
            import shutil

            disk = shutil.disk_usage(str(PROJECT_ROOT))
            free_disk_gb = disk.free / (1024**3)
            total_disk_gb = disk.total / (1024**3)

            console.print(
                f"  [bold]Logical CPUs:[/] {cpu_count} (Current Usage: {cpu_percent}%)"
            )
            console.print(
                f"  [bold]System Memory (RAM):[/] {used_ram_gb:.1f} GB used / {total_ram_gb:.1f} GB total ({free_ram_gb:.1f} GB free)"
            )
            console.print(
                f"  [bold]Disk Space:[/] {free_disk_gb:.1f} GB free / {total_disk_gb:.1f} GB total"
            )
        except Exception as e:
            console.print(f"[red]Error querying psutil metrics: {e}[/]")
    else:
        cpu_count = os.cpu_count() or 1
        import shutil

        try:
            disk = shutil.disk_usage(str(PROJECT_ROOT))
            free_disk_gb = disk.free / (1024**3)
            total_disk_gb = disk.total / (1024**3)
            console.print(f"  [bold]Logical CPUs:[/] {cpu_count}")
            console.print(
                f"  [bold]System Memory (RAM):[/] N/A (psutil module missing)"
            )
            console.print(
                f"  [bold]Disk Space:[/] {free_disk_gb:.1f} GB free / {total_disk_gb:.1f} GB total"
            )
        except Exception as e:
            console.print(f"[red]Error querying system resources: {e}[/]")
    console.print("[bold cyan]───────────────────────────────────────[/]\n")


def run_logs_check():
    """
    Tail the last 15 lines of log files.
    """
    log_files = ["aja_output.log", "autonomous_loop.log", "gateway.log"]
    console.print("\n Active Swarm & Gateway Logs (Last 15 Lines)")

    for filename in log_files:
        path = PROJECT_ROOT / filename
        console.print(f"\n📖 Log file: {filename}")
        if not path.exists():
            console.print("  (File does not exist yet or has no entries)")
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if not lines:
                console.print("  (Log is empty)")
                continue
            tail = lines[-15:]
            for line in tail:
                console.print(line.rstrip())
        except Exception as e:
            console.print(f"  [red]Error reading log: {e}[/]")

    console.print("──────────────────────────────────────────────────\n")
