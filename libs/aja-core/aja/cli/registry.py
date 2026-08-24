"""
AJA CLI Command Registry
========================
Dispatches subcommands to modular command implementations.

Consolidated user-facing surface (8 commands):
    aja            -> interactive chat REPL
    aja chat       -> force terminal REPL mode
    aja serve      -> headless 24/7 daemon (gateway + cron + autonomy)
    aja run        -> one-shot mission
    aja doctor     -> diagnostics
    aja setup      -> configuration wizard
    aja status     -> system status
    aja eval       -> evaluation framework
Plus low-priority helpers: mcp, pickup (internal), healthcheck.
"""

import sys
from typing import List, Callable, Dict, Optional
from aja.interface.modern import print_error, print_info


# Legacy command -> (replacement hint, target behavior)
_MIGRATION_HINTS: Dict[str, str] = {
    "ws": "use 'aja serve' (workspace scheduling now runs inside the serve daemon)",
    "workspace": "use 'aja serve'",
    "workspaces": "use 'aja serve'",
    "live": "use 'aja' (the default REPL includes the live experience)",
    "ui": "use 'aja' (the default REPL includes the TUI dashboard)",
    "tui": "use 'aja' (the default REPL includes the TUI dashboard)",
    "direct": "use 'aja chat'",
    "daemon": "use 'aja serve'",
    "rebuild-projections": "use 'aja doctor' (projections verified as part of diagnostics)",
}

_ALIASES: Dict[str, str] = {
    "ws": "serve",
    "workspace": "serve",
    "workspaces": "serve",
    "live": "chat",
    "ui": "chat",
    "tui": "chat",
    "direct": "chat",
    "daemon": "serve",
}


def _launch_repl():
    """Boots background components then launches the new streaming TerminalREPL."""
    import asyncio
    import os
    import subprocess

    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    gateway_proc = None
    worker_proc = None

    if token:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

        gateway_proc = subprocess.Popen(
            [sys.executable, "-m", "aja.gateway.server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        worker_proc = subprocess.Popen(
            [sys.executable, "-m", "aja.runtime.autonomous_loop"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    try:
        from aja.interface.repl import TerminalREPL

        repl = TerminalREPL()
        asyncio.run(repl.run())
    except KeyboardInterrupt:
        pass
    finally:
        import contextlib

        for proc in (gateway_proc, worker_proc):
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:  # best-effort: never block REPL exit on teardown
                    with contextlib.suppress(Exception):
                        proc.kill()


def _cmd_healthcheck(args: List[str]):
    """Lightweight liveness probe (no LanceDB opens) — safe as a container healthcheck."""
    import sys

    quick = "--quick" in args
    try:
        from aja.utils.startup_checks import (
            run_startup_checks,
            format_startup_checks,
            check_data_dir_writable,
        )

        results = []
        if not quick:
            results.extend(check_data_dir_writable())
            results.extend(run_startup_checks())
        else:
            results.append(check_data_dir_writable())

        failed = format_startup_checks(results)
        sys.exit(1 if failed else 0)
    except SystemExit:
        raise
    except Exception as e:  # fail loudly: a broken probe must not report healthy
        print_error(f"healthcheck failed: {e}")
        sys.exit(2)


class CommandRegistry:
    """
    Central dispatch registry for AJA CLI subcommands.
    """

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}

    def register(self, name: str, handler: Callable):
        self._handlers[name.lower()] = handler

    def _resolve(self, cmd: str) -> str:
        return _ALIASES.get(cmd, cmd)

    def _migration_notice(self, original: str):
        hint = _MIGRATION_HINTS.get(original)
        if hint:
            print_info(f"'aja {original}' is deprecated: {hint}.")

    def dispatch(self, args: List[str], agent_mode: bool = False):
        if not args:
            _launch_repl()
            return

        raw_cmd = args[0].lower()
        cmd = self._resolve(raw_cmd)

        # Surface deprecation hints for legacy names once, before dispatch.
        if raw_cmd in _MIGRATION_HINTS and raw_cmd != cmd:
            self._migration_notice(raw_cmd)

        if cmd in ("help", "--help", "-h"):
            from aja.cli.commands.help_cmd import show_help

            show_help(agent_mode=agent_mode)
            return

        # NOTE: self._handlers is registration-only API compat; dispatch is
        # intentionally explicit below so per-command flag parsing stays exact.
        if cmd == "run":
            from aja.cli.commands.run import cmd_run

            bg = "--bg" in args
            dry_run = "--dry-run" in args
            objective_parts = [a for a in args[1:] if a not in ("--bg", "--dry-run")]
            objective = " ".join(objective_parts)
            cmd_run(objective, background=bg, dry_run=dry_run)

        elif cmd == "chat":
            _launch_repl()

        elif cmd == "status":
            from aja.cli.commands.status import cmd_status

            cmd_status(agent_mode=agent_mode)

        elif cmd == "setup":
            from aja.cli.commands.setup import cmd_setup

            cmd_setup()

        elif cmd == "doctor":
            from aja.cli.commands.doctor import cmd_doctor

            ci_mode = "--ci" in args
            cmd_doctor(ci_mode=ci_mode, agent_mode=agent_mode)

        elif cmd == "healthcheck":
            _cmd_healthcheck(args[1:])

        elif cmd == "mcp":
            from aja.cli.commands.mcp_cmd import cmd_mcp

            cmd_mcp(args[1:])

        elif cmd == "exec":
            # Hidden helper (not part of the consolidated user surface).
            from aja.cli.commands.exec_cmd import cmd_exec

            cmd_exec(args[1:])

        elif cmd == "serve":
            from aja.cli.commands.serve_cmd import cmd_serve

            cmd_serve()

        elif cmd == "eval":
            from aja.cli.commands.eval_cmd import cmd_eval

            mode = next(
                (
                    a.split("=", 1)[1]
                    for a in args[1:]
                    if a.startswith("--mode=")
                ),
                None,
            )
            case = next(
                (a for a in args[1:] if not a.startswith("--")),
                None,
            )
            mission_id = next(
                (a.split("=", 1)[1] for a in args[1:] if a.startswith("--mission=")),
                None,
            )
            baseline = next(
                (a.split("=", 1)[1] for a in args[1:] if a.startswith("--baseline=")),
                None,
            )
            if baseline:
                cmd_eval(mode="gate", baseline=baseline)
            elif mode == "list" or not case:
                cmd_eval(mode="list")
            else:
                cmd_eval(mode="run", case=case, mission_id=mission_id)

        elif cmd == "pickup":
            # Internal-only: kept functional for fleet baton handover, hidden from help.
            from aja.cli.commands.pickup import cmd_pickup

            if len(args) < 2:
                print_error("Usage: aja pickup <code>")
            else:
                cmd_pickup(args[1])

        elif cmd == "reindex-embeddings":
            from aja.cli.commands.reindex_embeddings import cmd_reindex_embeddings

            cmd_reindex_embeddings()

        elif cmd == "rebuild-projections":
            # Deprecated: projections verification lives under `aja doctor`.
            self._migration_notice("rebuild-projections")
            from aja.cli.commands.projections import cmd_rebuild_projections

            cmd_rebuild_projections()

        else:
            from aja.cli.commands.help_cmd import show_help

            print_error(f"Unknown command: '{raw_cmd}'")
            show_help(agent_mode=agent_mode)


# Global singleton registry instance
registry = CommandRegistry()
