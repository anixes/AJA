"""
AJA CLI Command: local
======================
Interactive local model discovery, engine status, and model selector.
Supports automated CUDA llama-server launch for local GGUF models.
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

    # Check CLI arguments e.g. `aja local start` or `aja local qwen2.5-coder-7b...`
    arg_list = args.strip().split() if args else []
    if arg_list:
        sub = arg_list[0].lower()
        if sub == "start":
            eng = arg_list[1] if len(arg_list) > 1 else "llama"
            model_target = arg_list[2] if len(arg_list) > 2 else None
            success, msg = LocalModelManager.start_engine(eng, model=model_target)
            if success:
                console.print(f"[bold green]✔ {msg}[/bold green]")
            else:
                console.print(f"[bold yellow]! {msg}[/bold yellow]")
            return
        elif sub in ("list", "ls"):
            _display_models_table(console, LocalModelManager.discover_models())
            return
        else:
            # Direct model activation e.g. `aja local qwen2.5-coder-7b-instruct-q3_k_m.gguf`
            target = arg_list[0]
            if not any(target.startswith(p + ":") for p in ("ollama", "llama_cpp", "openai")):
                if target.endswith(".gguf"):
                    target = f"llama_cpp:{target}"
                else:
                    target = f"ollama:{target}"
            console.print(f"[cyan]Activating {target}...[/cyan]")
            LocalModelManager.activate_model(target)
            console.print(f"[bold green]✔ Active worker model switched to:[/] {target}")
            return

    # Interactive flow
    # 1. Probe engines
    statuses = LocalModelManager.probe_engines(timeout=1.0)
    engine_table = Table(title="Detected Local Engines", box=ROUNDED, expand=True)
    engine_table.add_column("Engine", style="bold white", width=16)
    engine_table.add_column("Status", width=14)
    engine_table.add_column("Endpoint", style="dim", width=28)
    engine_table.add_column("Models Ready", justify="right")

    for eng_key, status in statuses.items():
        if status.running:
            stat_str = "[bold green]Running[/]"
            count_str = f"[bold green]{status.models_count}[/] active"
        else:
            stat_str = "[dim red]Offline[/]"
            count_str = f"[dim]{status.error or 'Not running'}[/]"

        engine_table.add_row(status.name, stat_str, status.endpoint, count_str)

    console.print(engine_table)

    # 2. Discover models (running engines + GGUF models on disk)
    models = LocalModelManager.discover_models(timeout=1.5, include_disk=True)

    if models:
        _display_models_table(console, models)
        choices = {str(i + 1): m for i, m in enumerate(models)}
        choices["c"] = "Custom Model URI"
        choices["q"] = "Cancel"

        console.print("\n[bold]Select a model to activate (starts engine automatically if offline):[/bold]")
        for k, v in choices.items():
            if k in ("c", "q"):
                console.print(f"  [cyan]{k}[/]) {v}")
            else:
                source_tag = "[yellow](disk)[/]" if getattr(v, "details", {}).get("source") == "disk" else "[green](running)[/]"
                console.print(f"  [cyan]{k}[/]) [bold white]{v.name}[/] {source_tag} [dim]({v.engine})[/]")

        ans = Prompt.ask("\nEnter choice", choices=list(choices.keys()), default="1")
        if ans == "q":
            return
        elif ans == "c":
            custom_uri = Prompt.ask("Enter custom model URI (e.g. llama_cpp:model.gguf or ollama:qwen2.5-coder:7b)")
            if custom_uri:
                LocalModelManager.activate_model(custom_uri.strip())
                console.print(f"[bold green]✔ Successfully activated:[/] {custom_uri}")
        else:
            selected: LocalModelInfo = choices[ans]
            is_disk = selected.details.get("source") == "disk"
            if is_disk:
                console.print(f"[bold cyan]Starting llama-server with '{selected.name}'...[/bold cyan]")
            LocalModelManager.activate_model(selected.uri)
            console.print(f"[bold green]✔ Successfully activated:[/] {selected.uri}")
            console.print(f"[dim]AJA operating_mode set to 'hybrid' (direct local inference).[/]")
    else:
        console.print(
            Panel(
                "[yellow]No running local model engines or GGUF files detected.[/yellow]\n\n"
                "• [bold]llama.cpp[/]: Place .gguf files in [cyan]E:\\Models[/] or run [cyan]llama-server -m <model.gguf> --port 8080[/]\n"
                "• [bold]Ollama[/]: Run [cyan]ollama run qwen2.5-coder:7b[/]\n"
                "• [bold]LM Studio[/]: Enable Local Server on port 1234",
                title="Local Inference Setup",
                border_style="yellow",
            )
        )


def _display_models_table(console: "Console", models: list[LocalModelInfo]) -> None:
    table = Table(title="Available Local Models", box=ROUNDED, expand=True)
    table.add_column("#", width=4, justify="center")
    table.add_column("Model Name", style="bold white")
    table.add_column("Engine / Source", style="cyan", width=20)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Params", justify="center", width=8)
    table.add_column("Quant", justify="center", width=10)

    for i, m in enumerate(models, 1):
        size_str = f"{m.size_gb:.1f} GB" if m.size_gb else "-"
        param_str = m.parameter_size or "-"
        quant_str = m.quantization or "-"
        source_label = "llama.cpp (disk)" if m.details.get("source") == "disk" else m.engine
        table.add_row(str(i), m.name, source_label, size_str, param_str, quant_str)

    console.print(table)
