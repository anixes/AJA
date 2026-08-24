"""
AJA CLI Command: models
=======================
Interactive Copilot model selector for Swarm Planner / Single Agent Worker.
"""

import json
from typing import TYPE_CHECKING, Optional

from rich.prompt import Prompt

from aja.config import AJA_PLANNER_MODEL, AJA_WORKER_MODEL, DATA_DIR

if TYPE_CHECKING:
    from rich.console import Console


def handle_models_command(args: str = "", console: Optional["Console"] = None) -> None:
    """Select and persist new planner/worker models, interactively or via args."""
    if console is None:
        from aja.interface.modern import console  # type: ignore[no-redef]

    if args:
        parts = args.split()
        p_model = parts[0]
        w_model = parts[1] if len(parts) > 1 else parts[0]
    else:
        console.print(
            f"\n[bold cyan]Engine: Swarm Agents (Planner):[/] {AJA_PLANNER_MODEL}"
        )
        console.print(
            f"[bold cyan]Engine: Single Agent (Worker):[/] {AJA_WORKER_MODEL}"
        )
        choices_map = {
            "1": "copilot:gpt-4o",
            "2": "copilot:gpt-4o-mini",
            "3": "copilot:gpt-5.4-mini",
            "4": "copilot:gpt-5-mini",
            "5": "copilot:gpt-5.4",
            "6": "copilot:gpt-5.2",
            "7": "copilot:gpt-5.3-codex",
            "8": "copilot:gpt-5.5",
            "9": "copilot:claude-haiku-4.5",
            "10": "copilot:claude-sonnet-4.5",
            "11": "copilot:claude-sonnet-4.6",
            "12": "copilot:claude-opus-4.7",
            "13": "copilot:claude-opus-4.8",
            "14": "copilot:gemini-3.5-flash",
            "15": "Custom / Type your own",
            "16": "Cancel",
        }

        console.print("[bold]Select new models from Copilot:[/bold]")
        for k, v in choices_map.items():
            console.print(f"  {k}) {v}")

        p_choice = Prompt.ask(
            "\nSelect [bold cyan]Swarm Planner[/] option",
            choices=list(choices_map.keys()),
            default="16",
        )

        if p_choice == "16":
            return
        elif p_choice == "15":
            p_model = Prompt.ask("Enter Planner model (e.g. copilot:gpt-4o)")
        else:
            p_model = choices_map[p_choice]

        w_choice = Prompt.ask(
            "Select [bold cyan]Single Agent Worker[/] option (Press Enter to use same)",
            choices=list(choices_map.keys()) + [""],
            default="",
        )

        if w_choice == "" or w_choice == p_choice:
            w_model = p_model
        elif w_choice == "16":
            return
        elif w_choice == "15":
            w_model = Prompt.ask("Enter Worker model (e.g. copilot:gpt-4o-mini)")
        else:
            w_model = choices_map[w_choice]

    cfg_path = DATA_DIR / "aja.json"
    data = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f)

    if "swarm_settings" not in data:
        data["swarm_settings"] = {}
    if "models" not in data["swarm_settings"]:
        data["swarm_settings"]["models"] = {}

    data["swarm_settings"]["models"]["planner"] = p_model
    data["swarm_settings"]["models"]["worker"] = w_model

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    import aja.config

    aja.config.AJA_PLANNER_MODEL = p_model
    aja.config.AJA_WORKER_MODEL = w_model

    console.print("[green]✔ Successfully updated models![/green]")
    console.print(f"[bold cyan]Engine: Swarm Agents (Planner):[/] {p_model}")
    console.print(f"[bold cyan]Engine: Single Agent Worker:[/] {w_model}")
