"""
AJA CLI Command: setup
======================
Guided onboarding setup wizard for AJA.
"""

import json
import os
import shutil
from aja.config import CONFIG_PATH, DATA_DIR
from aja.interface.modern import console, print_error, print_info, print_success


def cmd_setup():
    """Guided onboarding setup wizard for AJA."""
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt

    console.print(
        Panel(
            "[bold cyan]Welcome to the AJA Setup Wizard[/]\n\n"
            "This tool will guide you through scaffolding directories, validating config keys, "
            "and setting up your local database files to ensure enterprise-grade product readiness.",
            title="AJA Onboarding",
            border_style="cyan",
        )
    )

    # Copy .env.example to .env if .env doesn't exist
    env_path = DATA_DIR / ".env"
    env_example_path = DATA_DIR / ".env.example"
    if not env_path.exists() and env_example_path.exists():
        console.print("[dim]No .env file found. Copying from .env.example...[/dim]")
        shutil.copy(env_example_path, env_path)

    # Check if config already exists
    if CONFIG_PATH.exists():
        recreate = Confirm.ask(
            "[yellow]An aja.json already exists. Re-configure?[/]", default=False
        )
        if not recreate:
            print_info("Skipping configuration generation. Verifying directories...")
            # Still initialize folders
            baton_dir = DATA_DIR / "batons"
            baton_dir.mkdir(parents=True, exist_ok=True)
            handover_dir = DATA_DIR / "handovers"
            handover_dir.mkdir(parents=True, exist_ok=True)
            print_success("Setup and directories verified.")
            return

    # Helper function to prompt for provider and model
    def ask_for_model(role_name: str):
        console.print(f"\n[bold magenta]--- Setup {role_name} ---[/bold magenta]")
        providers = {
            "1": "copilot",
            "2": "openai",
            "3": "anthropic",
            "4": "google",
            "5": "llama_cpp",
        }
        console.print("Select Provider:")
        for k, v in providers.items():
            console.print(f"  {k}) {v}")
        p_choice = Prompt.ask(
            "Provider Option", choices=list(providers.keys()), default="1"
        )
        provider = providers[p_choice]

        if provider == "copilot":
            models = ["gpt-4o", "gpt-4o-mini", "claude-haiku-4.5", "claude-sonnet-4.6"]
        elif provider == "openai":
            models = ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"]
        elif provider == "anthropic":
            models = ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"]
        elif provider == "google":
            models = ["gemini-2.5-flash", "gemini-2.5-pro"]
        elif provider == "llama_cpp":
            model_name = Prompt.ask(
                f"Enter Local Model name for {role_name} (e.g. gemma-2-9b-it)"
            )
            return f"{provider}:{model_name}", provider

        console.print(f"Select Top {provider.capitalize()} Model:")
        for i, m in enumerate(models, 1):
            console.print(f"  {i}) {m}")
        console.print(f"  {len(models) + 1}) Custom / Type your own")

        m_choice = Prompt.ask(
            "Model Option",
            choices=[str(i) for i in range(1, len(models) + 2)],
            default="1",
        )
        if int(m_choice) <= len(models):
            model_name = models[int(m_choice) - 1]
        else:
            model_name = Prompt.ask(f"Enter Custom {provider} Model name")

        return f"{provider}:{model_name}", provider

    # Helper function to securely update .env keys
    def update_env_key(key: str, value: str):
        if not value:
            return
        if not env_path.exists():
            env_path.touch()
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = [l for l in lines if not l.startswith(f"{key}=")]
        new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    project_name = Prompt.ask("\nEnter Project Name", default="AJA")

    operating_mode = Prompt.ask(
        "Choose Operating Mode",
        choices=["offline", "online", "hybrid"],
        default="hybrid",
    )

    console.print(
        "\n[dim]The Swarm Planner orchestrates high-level tasks, while the Single Agent Worker executes individual steps.[/dim]"
    )
    planner_model, planner_provider = ask_for_model("Swarm Planner")
    console.print(
        "[dim]* Note: The Swarm Critic model is automatically linked to your Planner model for onboarding simplicity. Separating the roles guarantees opposing system prompts (Builder vs Attacker) for higher quality results.[/dim]"
    )
    worker_model, worker_provider = ask_for_model("Single Agent Worker")

    console.print("\n[bold magenta]--- API Key Validation ---[/bold magenta]")
    required_providers = set([planner_provider, worker_provider])
    if "openai" in required_providers:
        if not os.environ.get("OPENAI_API_KEY"):
            val = Prompt.ask(
                "Enter OPENAI_API_KEY (or press Enter to skip)",
                password=True,
                default="",
            )
            update_env_key("OPENAI_API_KEY", val)
    if "anthropic" in required_providers:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            val = Prompt.ask(
                "Enter ANTHROPIC_API_KEY (or press Enter to skip)",
                password=True,
                default="",
            )
            update_env_key("ANTHROPIC_API_KEY", val)
    if "google" in required_providers:
        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get(
            "GOOGLE_API_KEY"
        ):
            val = Prompt.ask(
                "Enter GEMINI_API_KEY (or press Enter to skip)",
                password=True,
                default="",
            )
            update_env_key("GEMINI_API_KEY", val)
            update_env_key("GOOGLE_API_KEY", val)

    console.print("\n[bold magenta]--- Platform Integrations ---[/bold magenta]")
    if Confirm.ask("Do you want to configure a Telegram Bot token now?", default=False):
        t_token = Prompt.ask("Enter TELEGRAM_BOT_TOKEN", password=True)
        update_env_key("TELEGRAM_BOT_TOKEN", t_token)

    config_data = {
        "project_name": project_name,
        "territories": [
            {
                "path": "libs/aja-core",
                "health_cmd": "python -m aja status",
                "auto_heal": False,
            },
        ],
        "swarm_settings": {
            "offline_mode": operating_mode == "offline",
            "max_agents": 5,
            "check_interval": 30,
            "models": {
                "planner": planner_model,
                "worker": worker_model,
                "critic": planner_model,
            },
            "operating_mode": operating_mode,
        },
    }

    try:
        from aja.config_schema import AJAConfig

        AJAConfig.model_validate(config_data)

        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        print_success(f"\nSuccessfully generated and validated {CONFIG_PATH}")
    except Exception as e:
        print_error(f"Failed to validate generated configuration: {e}")
        return

    baton_dir = DATA_DIR / "batons"
    baton_dir.mkdir(parents=True, exist_ok=True)
    handover_dir = DATA_DIR / "handovers"
    handover_dir.mkdir(parents=True, exist_ok=True)
    print_success("Vector store database directories successfully initialized.")
    console.print(
        "\n[bold green]Setup Complete! You can now run `aja chat`.[/bold green]"
    )
