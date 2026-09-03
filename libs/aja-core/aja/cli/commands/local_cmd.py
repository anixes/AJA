"""
AJA CLI Command: local
======================
Interactive local model discovery, engine status, and model selector.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Optional

from rich.box import ROUNDED
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from aja.models.local_manager import LocalModelInfo, LocalModelManager

if TYPE_CHECKING:
    from rich.console import Console


def cmd_local(args: str = "", console: Optional["Console"] = None) -> None:
    """Discover, start, and select locally running models."""
    if console is None:
        from aja.interface.modern import console as default_console
        console = default_console

    active = LocalModelManager.get_active_model()
    console.print("\n[bold cyan]═══ Local Model Manager ═══[/]")
    console.print(f"[dim]Current Worker Model:[/] [bold green]{active['worker']}[/]")

    # If specific model name or action passed directly
    arg_list = args.strip().split() if args else []
    if arg_list:
        sub = arg_list[0].lower()
        if sub == "start":
            success, msg = LocalModelManager.start_engine("ollama")
            if success:
                console.print(f"[green]✔ {msg}[/green]")
            else:
                console.print(f"[yellow]! {msg}[/yellow]")
            return
        elif sub in ("list", "ls"):
            _display_models_table(console, LocalModelManager.discover_models())
            return
        else:
            # Direct model activation e.g. `aja local qwen2.5-coder:7b`
            target = arg_list[0]
            if not any(target.startswith(p + ":") for p in ("ollama", "llama_cpp", "openai")):
                target = f"ollama:{target}"
            LocalModelManager.activate_model(target)
            console.print(f"[bold green]✔ Active worker model switched to:[/] {target}")
            return

    # Interactive flow
    # 1. Probe engines
    statuses = LocalModelManager.probe_engines(timeout=1.0)
    engine_table = Table(title="Detected Local Engines", box=ROUNDED, expand=True)
    engine_table.add_column("Engine", style="bold white", width=16)
    engine_table.add_column("Status", width=12)
    engine_table.add_column("Endpoint", style="dim", width=28)
    engine_table.add_column("Available Models", justify="right")

    for eng_key, status in statuses.items():
        if status.running:
            stat_str = "[bold green]Running[/]"
            count_str = f"[bold green]{status.models_count}[/] model(s)"
        else:
            stat_str = "[dim red]Offline[/]"
            count_str = f"[dim]{status.error or 'Not running'}[/]"

        engine_table.add_row(status.name, stat_str, status.endpoint, count_str)

    console.print(engine_table)

    # 2. Discover models
    models = LocalModelManager.discover_models(timeout=1.5)

    if models:
        _display_models_table(console, models)
        choices = {str(i + 1): m for i, m in enumerate(models)}
        choices["c"] = "Custom Model URI"
        choices["q"] = "Cancel"

        console.print("\n[bold]Select a model to activate for AJA:[/bold]")
        for k, v in choices.items():
            if k in ("c", "q"):
                console.print(f"  [cyan]{k}[/]) {v}")
            else:
                console.print(f"  [cyan]{k}[/]) [bold white]{v.name}[/] [dim]({v.engine})[/]")

        ans = Prompt.ask("\nEnter choice", choices=list(choices.keys()), default="1")
        if ans == "q":
            return
        elif ans == "c":
            custom_uri = Prompt.ask("Enter custom model URI (e.g. ollama:qwen2.5-coder:7b)")
            if custom_uri:
                LocalModelManager.activate_model(custom_uri.strip())
                console.print(f"[bold green]✔ Successfully activated:[/] {custom_uri}")
        else:
            selected: LocalModelInfo = choices[ans]
            LocalModelManager.activate_model(selected.uri)
            console.print(f"[bold green]✔ Successfully activated:[/] {selected.uri}")
            console.print(f"[dim]AJA operating_mode set to 'hybrid' (direct local inference).[/]")
    else:
        console.print(
            Panel(
                "[yellow]No running local model engines detected on standard ports.[/yellow]\n\n"
                "• [bold]Ollama[/]: Run [cyan]ollama run qwen2.5-coder:7b[/] in another terminal, or\n"
                "• [bold]Docker[/]: [cyan]docker run -d -p 11434:11434 --name ollama ollama/ollama[/]\n"
                "• [bold]llama.cpp[/]: Run [cyan]llama-server -m <model.gguf> --port 8080[/]\n"
                "• [bold]LM Studio[/]: Enable Local Server on port 1234",
                title="Local Inference Setup",
                border_style="yellow",
            )
        )

        ollama_status = statuses.get("ollama")
        if ollama_status and ollama_status.installed:
            start_ans = Prompt.ask(
                "Ollama CLI is installed. Start Ollama background service now?",
                choices=["y", "n"],
                default="y",
            )
            if start_ans == "y":
                success, msg = LocalModelManager.start_engine("ollama")
                if success:
                    console.print(f"[green]✔ {msg}[/green]")
                    console.print("Run [bold cyan]aja local[/] in a few seconds once models are loaded.")
                else:
                    console.print(f"[red]✘ {msg}[/red]")


def _display_models_table(console: "Console", models: list[LocalModelInfo]) -> None:
    table = Table(title="Locally Installed Models", box=ROUNDED, expand=True)
    table.add_column("#", width=4, justify="center")
    table.add_column("Model Name", style="bold white")
    table.add_column("Engine", style="cyan", width=12)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Params", justify="center", width=8)
    table.add_column("Quant", justify="center", width=10)

    for i, m in enumerate(models, 1):
        size_str = f"{m.size_gb:.1f} GB" if m.size_gb else "-"
        param_str = m.parameter_size or "-"
        quant_str = m.quantization or "-"
        table.add_row(str(i), m.name, m.engine, size_str, param_str, quant_str)

    console.print(table)
