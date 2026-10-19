"""
AJA CLI Command: models
=======================
Interactive Copilot model selector for Swarm Planner / Single Agent Worker.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from rich.prompt import Prompt

from aja.config import AJA_PLANNER_MODEL, AJA_WORKER_MODEL, CONFIG_PATH, DATA_DIR

logger = logging.getLogger(__name__)

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
            "15": "Local Models (Scan Ollama, llama.cpp, LM Studio)",
            "16": "Custom / Type your own",
            "17": "Cancel",
        }

        console.print("[bold]Select new models:[/bold]")
        for k, v in choices_map.items():
            console.print(f"  {k}) {v}")

        p_choice = Prompt.ask(
            "\nSelect [bold cyan]Swarm Planner[/] option",
            choices=list(choices_map.keys()),
            default="17",
        )

        if p_choice == "17":
            return
        elif p_choice == "15":
            from aja.cli.commands.local_cmd import cmd_local

            cmd_local(console=console)
            return
        elif p_choice == "16":
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
        elif w_choice == "17":
            return
        elif w_choice == "15":
            from aja.cli.commands.local_cmd import cmd_local

            cmd_local(console=console)
            return
        elif w_choice == "16":
            w_model = Prompt.ask("Enter Worker model (e.g. copilot:gpt-4o-mini)")
        else:
            w_model = choices_map[w_choice]

    def _atomic_write_model_config(target_path: Path, model_name: str) -> None:
        cfg_data = {}
        if target_path.exists():
            try:
                with open(target_path, encoding="utf-8") as f:
                    cfg_data = json.load(f)
            except Exception as read_err:
                logger.debug("Failed reading existing config at %s: %s", target_path, read_err)

        if "swarm_settings" not in cfg_data:
            cfg_data["swarm_settings"] = {}

        cfg_data["swarm_settings"]["active_model"] = model_name
        # Clean out legacy zombie role keys if present, preserving any other keys like vision
        if "models" in cfg_data.get("swarm_settings", {}):
            models_dict = cfg_data["swarm_settings"].get("models")
            if isinstance(models_dict, dict):
                for k in ("planner", "worker", "critic"):
                    models_dict.pop(k, None)
                if not models_dict:
                    cfg_data["swarm_settings"].pop("models", None)
            else:
                cfg_data["swarm_settings"].pop("models", None)

        tmp_path = target_path.with_suffix(".tmp")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg_data, f, indent=4)

        # Retry replace for Windows file locks
        for attempt in range(5):
            try:
                os.replace(tmp_path, target_path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

    cfg_path = CONFIG_PATH or (DATA_DIR / "aja.json")
    _atomic_write_model_config(cfg_path, p_model)

    # If cfg_path is local workspace, synchronize DATA_DIR / aja.json
    data_cfg = DATA_DIR / "aja.json"
    if cfg_path.resolve() != data_cfg.resolve():
        try:
            _atomic_write_model_config(data_cfg, p_model)
        except Exception as e:
            logger.warning("Failed to synchronize global config at %s: %s", data_cfg, e)

    import aja.config

    aja.config.AJA_ACTIVE_MODEL = p_model
    aja.config.AJA_PLANNER_MODEL = p_model
    aja.config.AJA_WORKER_MODEL = p_model

    console.print("[green]✔ Successfully updated models![/green]")
    console.print(f"[bold cyan]Engine: Active Model:[/] {p_model}")
