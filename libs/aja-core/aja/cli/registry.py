"""
AJA CLI Command Registry
========================
Dispatches subcommands to modular command implementations.
"""

from typing import List, Callable, Dict
from aja.interface.modern import print_error


class CommandRegistry:
    """
    Central dispatch registry for AJA CLI subcommands.
    """

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}

    def register(self, name: str, handler: Callable):
        self._handlers[name.lower()] = handler

    def dispatch(self, args: List[str], agent_mode: bool = False):
        if not args:
            from aja.cli.commands.chat import run_chat_with_gateway

            run_chat_with_gateway()
            return

        cmd = args[0].lower()

        if cmd in ("help", "--help", "-h"):
            from aja.cli.commands.help_cmd import show_help

            show_help(agent_mode=agent_mode)
            return

        if cmd == "run":
            from aja.cli.commands.run import cmd_run

            bg = "--bg" in args
            dry_run = "--dry-run" in args
            objective_parts = [a for a in args[1:] if a not in ("--bg", "--dry-run")]
            objective = " ".join(objective_parts)
            cmd_run(objective, background=bg, dry_run=dry_run)

        elif cmd == "direct":
            from aja.cli.commands.direct import cmd_direct

            dry_run = "--dry-run" in args
            resume = "--resume" in args
            model = next(
                (a.split("=", 1)[1] for a in args if a.startswith("--model=")), None
            )
            cmd_direct(dry_run=dry_run, model=model, resume=resume)

        elif cmd == "chat":
            from aja.cli.commands.chat import run_chat_with_gateway

            run_chat_with_gateway()

        elif cmd == "status":
            from aja.cli.commands.status import cmd_status

            cmd_status(agent_mode=agent_mode)

        elif cmd == "setup":
            from aja.cli.commands.setup import cmd_setup

            cmd_setup()

        elif cmd == "daemon":
            from aja.cli.commands.daemon_cmd import cmd_daemon

            cmd_daemon(args[1:])

        elif cmd == "doctor":
            from aja.cli.commands.doctor import cmd_doctor

            ci_mode = "--ci" in args
            cmd_doctor(ci_mode=ci_mode, agent_mode=agent_mode)

        elif cmd == "exec":
            from aja.cli.commands.exec_cmd import cmd_exec

            cmd_exec(args[1:])

        elif cmd == "mcp":
            from aja.cli.commands.mcp_cmd import cmd_mcp

            cmd_mcp(args[1:])

        elif cmd == "live":
            from aja.cli.commands.tui_cmd import cmd_live

            cmd_live()

        elif cmd == "ui":
            from aja.cli.commands.tui_cmd import cmd_ui

            cmd_ui()

        elif cmd == "pickup":
            from aja.cli.commands.pickup import cmd_pickup

            if len(args) < 2:
                print_error("Usage: aja pickup <code>")
            else:
                cmd_pickup(args[1])

        elif cmd == "tui":
            from aja.cli.commands.tui_cmd import cmd_tui

            dry_run = "--dry-run" in args
            cmd_tui(dry_run=dry_run)

        elif cmd in ("ws", "workspace", "workspaces"):
            from aja.cli.commands.ws_cmd import cmd_ws

            cmd_ws(args[1:])

        elif cmd == "rebuild-projections":
            from aja.cli.commands.projections import cmd_rebuild_projections

            cmd_rebuild_projections()

        else:
            from aja.cli.commands.help_cmd import show_help

            print_error(f"Unknown command: '{cmd}'")
            show_help(agent_mode=agent_mode)


# Global singleton registry instance
registry = CommandRegistry()
