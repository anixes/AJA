"""
AJA CLI Command: help
=====================
Displays the AJA Command Suite overview.
"""

import json
from pathlib import Path
from aja.config import PROJECT_ROOT
from aja.interface.modern import console


def parse_frontmatter_meta(file_path: Path) -> dict:
    if not file_path.exists():
        return {}
    try:
        content = file_path.read_text(encoding="utf-8")
        import re

        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if match:
            metadata = {}
            for line in match.group(1).split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    metadata[k.strip()] = v.strip().strip('"').strip("'")
            return metadata
    except Exception:
        pass
    return {}


def show_help(agent_mode: bool = False):
    """Displays the AJA Command Suite."""
    if agent_mode:
        rules = []
        skills = []

        brief_text = "AJA Orchestration Engine"
        brief_path = PROJECT_ROOT / "agent" / "brief.md"
        if brief_path.exists():
            brief_text = brief_path.read_text(encoding="utf-8").strip()

        rules_dir = PROJECT_ROOT / "agent" / "rules"
        if rules_dir.exists():
            for p in rules_dir.glob("*.md"):
                meta = parse_frontmatter_meta(p)
                rules.append(
                    {
                        "name": meta.get("name", p.stem),
                        "description": meta.get(
                            "description", "AJA trigger/workflow constraint rules file."
                        ),
                    }
                )

        skills_dir = PROJECT_ROOT / "agent" / "skills"
        if skills_dir.exists():
            for p in skills_dir.glob("*.md"):
                meta = parse_frontmatter_meta(p)
                skills.append(
                    {
                        "name": meta.get("name", p.stem),
                        "description": meta.get(
                            "description", "AJA extended skills documentation."
                        ),
                    }
                )

        help_json = {
            "help": brief_text,
            "commands": [
                {
                    "name": "run",
                    "description": "Start an autonomous mission with the given objective.",
                    "parameters": [
                        {"name": "<objective>", "type": "string", "required": True},
                        {
                            "name": "--dry-run",
                            "type": "boolean",
                            "required": False,
                            "description": "Run simulation without making mutations",
                        },
                        {
                            "name": "--bg",
                            "type": "boolean",
                            "required": False,
                            "description": "Run in background process group",
                        },
                    ],
                },
                {
                    "name": "chat",
                    "description": "Launch the interactive conversational assistant loop.",
                },
                {
                    "name": "status",
                    "description": "Show active swarm health, batons, and pending tasks.",
                },
                {
                    "name": "doctor",
                    "description": "Run environment readiness and diagnostics checks.",
                },
                {
                    "name": "serve",
                    "description": "Run the headless 24/7 daemon (gateway + cron + autonomy).",
                },
                {
                    "name": "eval",
                    "description": "Evaluation framework: run a case or gate against a baseline.",
                },
                {
                    "name": "setup",
                    "description": "Run the interactive configuration wizard.",
                },
                {
                    "name": "healthcheck",
                    "description": "Lightweight liveness probe; exits non-zero on failure.",
                    "parameters": [
                        {
                            "name": "--quick",
                            "type": "boolean",
                            "required": False,
                            "description": "Minimal in-memory checks only",
                        }
                    ],
                },
            ],
            "rules": rules
            if rules
            else [
                {
                    "name": "trigger",
                    "description": "When should an agent use this tool",
                },
                {"name": "workflow", "description": "Step-by-step usage flow"},
                {"name": "writeback", "description": "How to write feedback back"},
            ],
            "skills": skills
            if skills
            else [
                {
                    "name": "getting-started",
                    "description": "Technical onboarding guide to write durable activities",
                }
            ],
        }
        print(json.dumps(help_json, indent=2), flush=True)
        return

    from rich.panel import Panel

    help_text = """
[bold cyan]Daily Use[/]
[green]aja[/]                  → Interactive chat REPL (default)
[green]chat[/]               → Force terminal REPL mode
[green]serve[/]              → Headless 24/7 daemon (gateway + cron + autonomy)
[green]run[/] <objective> [--dry-run] [--bg] → One-shot autonomous mission

[bold cyan]System & Ops[/]
[yellow]doctor[/] [--ci]      → Diagnostics (incl. projection verification)
[yellow]setup[/]              → Configuration wizard
[yellow]status[/]             → System status (missions, workers, batons)
[yellow]eval[/] <case>        → Evaluation framework (`--mode=list`, `--baseline=<f>` gate)
[yellow]healthcheck[/] [--quick] → Lightweight liveness probe (container-safe)

[dim]Also available: mcp (MCP server tools), pickup <code> (internal baton resume).[/]

[dim bold]Migrations (removed aliases):[/dim]
[dim]  ws / daemon → aja serve · direct → aja chat[/dim]
[dim]  live / ui / tui → aja (default REPL) · rebuild-projections → aja doctor[/dim]
    """
    console.print(Panel(help_text, title="AJA Command Suite", border_style="cyan"))
