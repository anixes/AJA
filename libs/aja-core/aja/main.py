"""
AJA — Unified CLI Entry Point
=================================
The central nervous system of the AJA swarm.
Lightweight dispatcher routing commands to aja.cli components.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment secrets
load_dotenv(override=True)

from aja.config import PROJECT_ROOT, CONFIG_PATH, DATA_DIR
from aja.cli.registry import registry

# Re-export command handlers for backwards compatibility
from aja.cli.commands.run import cmd_run
from aja.cli.commands.direct import cmd_direct
from aja.cli.commands.chat import cmd_chat, run_chat_with_gateway
from aja.cli.commands.status import cmd_status, run_gpu_check, run_logs_check
from aja.cli.commands.setup import cmd_setup
from aja.cli.commands.doctor import cmd_doctor
from aja.cli.commands.daemon_cmd import cmd_daemon
from aja.cli.commands.exec_cmd import cmd_exec
from aja.cli.commands.mcp_cmd import cmd_mcp
from aja.cli.commands.pickup import cmd_pickup
from aja.cli.commands.tui_cmd import cmd_tui, cmd_live, cmd_ui
from aja.cli.commands.projections import cmd_rebuild_projections
from aja.cli.commands.ws_cmd import cmd_ws
from aja.cli.commands.help_cmd import show_help

AGENT_MODE = False


def main():
    global AGENT_MODE
    args = sys.argv[1:]

    # Intercept --brief
    if "--brief" in args:
        brief_path = PROJECT_ROOT / "agent" / "brief.md"
        if brief_path.exists():
            print(brief_path.read_text(encoding="utf-8").strip(), flush=True)
        else:
            print("AJA Orchestration Engine", flush=True)
        sys.exit(0)

    # Process explicit agent/human mode flags
    has_agent = "--agent" in args
    has_human = "--human" in args

    # Remove spec flags from command-line arguments list
    args = [a for a in args if a not in ("--agent", "--human")]

    if has_agent:
        AGENT_MODE = True
    elif has_human:
        AGENT_MODE = False
    else:
        # Default to agent mode (JSON) if stdout is piped or redirected, else human mode
        AGENT_MODE = not sys.stdout.isatty()

    # Dispatch subcommand via CLI Command Registry
    registry.dispatch(args, agent_mode=AGENT_MODE)


if __name__ == "__main__":
    main()
