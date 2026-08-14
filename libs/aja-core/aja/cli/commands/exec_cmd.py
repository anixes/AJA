"""
AJA CLI Command: exec
=====================
Inspect execution sessions, timelines, and diffs.
"""

import json
import subprocess
from typing import List
from aja.config import DATA_DIR, PROJECT_ROOT
from aja.interface.modern import console, print_error, print_success


def cmd_exec(args: List[str]):
    """Inspect canonical execution runtime sessions and artifacts."""
    from rich.table import Table
    from aja.runtime.execution import get_default_execution_manager

    manager = get_default_execution_manager()
    subcmd = args[0].lower() if args else "list"
    exec_root = DATA_DIR / "executions"

    if subcmd == "list":
        table = Table(title="Execution Sessions")
        table.add_column("Session")
        table.add_column("State")
        table.add_column("Started")
        table.add_column("Command")

        active = {item["session_id"]: item for item in manager.list_active()}
        seen = set()
        for session_id, item in active.items():
            seen.add(session_id)
            table.add_row(
                session_id,
                item.get("state", "unknown"),
                item.get("started_at") or "-",
                item.get("command", "")[:80],
            )

        if exec_root.exists():
            for path in sorted(
                exec_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
            ):
                if not path.is_dir() or path.name in seen:
                    continue
                result_path = path / "result.json"
                manifest_path = path / "manifest.json"
                state = "unknown"
                started = "-"
                command = ""
                if result_path.exists():
                    try:
                        result = json.loads(result_path.read_text(encoding="utf-8"))
                        state = result.get("state", "unknown")
                        started = result.get("started_at", "-")
                    except Exception:
                        pass
                if manifest_path.exists():
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        command = manifest.get("command", "")
                    except Exception:
                        pass
                table.add_row(path.name, state, started, command[:80])
        console.print(table)
        return

    if len(args) < 2 and subcmd not in {"cleanup", "replay"}:
        print_error(
            "Usage: aja exec <show|timeline|diff|apply|replay|cleanup> <session_id>"
        )
        return

    if subcmd == "cleanup":
        removed = manager.cleanup_stale()
        print_success(f"Removed {len(removed)} stale execution workspaces.")
        return

    if subcmd == "replay":
        if len(args) < 2 or args[1] == "--latest":
            latest = None
            latest_time = 0
            if exec_root.exists():
                for p in exec_root.iterdir():
                    if p.is_dir():
                        mtime = p.stat().st_mtime
                        if mtime > latest_time:
                            latest_time = mtime
                            latest = p.name
            if not latest:
                print_error("No execution sessions found.")
                return
            session_id = latest
        else:
            session_id = args[1]

        session_root = exec_root / session_id
        if not session_root.exists():
            print_error(f"No execution session found: {session_id}")
            return

        from aja.tui.replay_viewer import run_replay

        run_replay(session_id, exec_root)
        return

    session_id = args[1]
    session_root = exec_root / session_id
    if subcmd == "show":
        result_path = session_root / "result.json"
        manifest_path = session_root / "manifest.json"
        if not result_path.exists() and not manifest_path.exists():
            print_error(f"No execution session found: {session_id}")
            return
        if manifest_path.exists():
            console.print_json(manifest_path.read_text(encoding="utf-8"))
        if result_path.exists():
            console.print_json(result_path.read_text(encoding="utf-8"))
        return

    if subcmd == "timeline":
        timeline = manager.get_timeline(session_id)
        for event in timeline:
            console.print(
                f"[dim]{event.get('timestamp', '-')}[/] [cyan]{event.get('event_type', '-')}[/] {event.get('message', '')}"
            )
        return

    if subcmd == "diff":
        diff = manager.get_diff(session_id)
        console.print_json(json.dumps(diff, indent=2))
        return

    if subcmd == "apply":
        diff = manager.get_diff(session_id)
        if not diff.get("diff_text"):
            print_error(
                f"No patch diff found for session {session_id} or no changes were made."
            )
            return

        patch_text = diff["diff_text"]
        patch_file = session_root / "apply.patch"
        patch_file.write_text(patch_text, encoding="utf-8")

        console.print(f"[*] Validating patch for session [bold]{session_id}[/bold]...")
        res_check = subprocess.run(
            ["git", "apply", "--check", "--binary", str(patch_file)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        if res_check.returncode != 0:
            print_error(
                f"Patch validation failed:\n{res_check.stderr or res_check.stdout}"
            )
            return

        console.print(
            "[green][*] Validation passed. Applying patch to project root...[/green]"
        )
        res_apply = subprocess.run(
            ["git", "apply", "--binary", str(patch_file)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        if res_apply.returncode == 0:
            print_success(
                f"Successfully applied isolated workspace changes for session {session_id}."
            )
        else:
            print_error(
                f"Failed to apply patch:\n{res_apply.stderr or res_apply.stdout}"
            )
        return

    print_error(f"Unknown exec command: {subcmd}")
