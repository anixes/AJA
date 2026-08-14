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
                    "name": "pickup",
                    "description": "Resume a mission from a high-performance Arrow Baton code.",
                    "parameters": [
                        {"name": "<code>", "type": "string", "required": True}
                    ],
                },
                {
                    "name": "tui",
                    "description": "Run the live terminal curses TUI dashboard.",
                },
                {
                    "name": "rebuild-projections",
                    "description": "Rebuild derived LanceDB projections from append-only journals.",
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
[bold cyan]Core Mission Commands[/]
[green]swarm[/] <objective> [--dry-run] → Start a mission (with optional simulation)
[green]direct[/] [--dry-run] [--model=<m>] [--resume] → Persistent interactive developer session
[green]chat[/]              → Interactive conversational loop
[green]status[/]            → Show swarm health
[green]pickup[/] <code>      → Resume a mission
[green]tui[/] [--dry-run]     → Run premium live HTN dashboard

[bold cyan]System Commands[/]
[yellow]setup[/]              → Onboarding setup wizard
[yellow]mode[/] <mode>        → Set mode (offline/online/hybrid)
[yellow]doctor[/]             → Run diagnostics
[yellow]metrics[/]            → View performance
[yellow]exec[/] <cmd>          → Inspect execution sessions, timelines, and diffs
[yellow]mcp reload[/] <server> → Reload MCP server tools
[yellow]rebuild-projections[/] → Rebuild derived LanceDB read projections
    """
    console.print(Panel(help_text, title="AJA Command Suite", border_style="cyan"))
