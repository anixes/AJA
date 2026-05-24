from __future__ import annotations

import os
import signal
from typing import List, Optional

from aja.runtime.execution.contracts import ProcessSnapshot


def snapshot_process(pid: Optional[int], returncode: Optional[int] = None) -> ProcessSnapshot:
    if not pid:
        return ProcessSnapshot(pid=pid, returncode=returncode)
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        children = [child.pid for child in proc.children(recursive=True)]
        mem = proc.memory_info().rss
        return ProcessSnapshot(
            pid=pid,
            returncode=returncode,
            children=children,
            cpu_percent=proc.cpu_percent(interval=None),
            memory_rss=mem,
        )
    except Exception:
        return ProcessSnapshot(pid=pid, returncode=returncode)


def terminate_tree(pid: Optional[int], force: bool = False) -> List[int]:
    """Terminate only the process tree rooted at pid."""
    if not pid:
        return []
    killed: List[int] = []
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        targets = proc.children(recursive=True) + [proc]
        for target in targets:
            try:
                if force:
                    target.kill()
                else:
                    target.terminate()
                killed.append(target.pid)
            except Exception:
                continue
        return killed
    except Exception:
        pass

    try:
        if os.name == "nt":
            import subprocess

            cmd = ["taskkill", "/PID", str(pid), "/T"]
            if force:
                cmd.append("/F")
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
        else:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.killpg(pid, sig)
        killed.append(pid)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
            killed.append(pid)
        except Exception:
            pass
    return killed
