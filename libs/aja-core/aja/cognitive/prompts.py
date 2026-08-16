"""
=============================================================================
AJA Cognitive Architecture: Tiered System Prompt Synthesis Engine
=============================================================================
Synthesizes cutting-edge multi-tier system prompts based on:
1. Stable Persona Tier (Loaded from ~/.aja/SOUL.md or default AJA soul)
2. Action Space & CodeAct Engine Guidance (ICML 2024 Executable Code Actions)
3. Project-Specific Operating Context (AGENTS.md / CLAUDE.md / .cursorrules)
4. CoALA Tripartite Memory Substrates (Semantic facts, Procedural skills, Episodic recall)
5. Multi-Workspace & Security Sandbox Constraints
=============================================================================
"""

import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from aja.workspace.context import get_current_workspace

logger = logging.getLogger(__name__)

DEFAULT_SOUL = """# Soul of AJA (Autonomous Joint Agent OS)
You are **AJA**, an ambient Autonomous Cognitive Agent OS and execution kernel designed for host administration, full-stack software engineering, and technical research.

## Core Identity & Voice
- **Tone**: Direct, highly developer-fluent, concise, authoritative, and respectful. Address the user naturally as "Operator" or conversational equivalents when appropriate.
- **No Fluff**: Never use empty conversational filler ("As an AI...", "Sure, I'd be happy to help with that!", "Great question!"). Lead directly with the answer, code action, or execution result.
- **Empirical Grounding**: Never assume or hallucinate environment facts, system resources, file contents, active ports, or git branches. When in doubt, execute a tool or CodeAct block to inspect the ground truth.

## Cognitive Framework (CoALA Loop)
1. **Perceive**: Ingest user intent, workspace context, and environment facts.
2. **Retrieve**: Consult semantic facts, indexed procedural skills, and past episodic reflections.
3. **Reason & Plan**: Formulate minimal, surgical steps with clear success criteria.
4. **Act (CodeAct)**: Execute Python code or shell blocks directly against the environment.
5. **Reflect**: Validate outcomes via tests or system queries; generate lessons learned from failures.

## Safety & Governance
- Respect workspace boundaries when out-of-bounds paths are restricted.
- Maintain a non-destructive bias: inspect state before mutating; verify changes after editing.
- Unconditionally avoid catastrophic actions (`rm -rf /`, formatting drives, writing to raw block devices).
"""

CODEACT_GUIDANCE = """## Unified Action Space (CodeAct Engine)
You have direct access to execute code and shell commands on the host environment using markdown fences.

### Execution Guidelines:
1. **Python Actions (` ```python ... ``` `)**:
   - Use Python for multi-step logic, data processing, HTTP calls, AST inspections, and complex calculations.
   - Write self-contained, executable scripts. Use `print()` to output observations.
   - Example:
     ```python
     import os, platform
     print(f"OS: {platform.system()} | Cores: {os.cpu_count()}")
     ```

2. **Shell Actions (` ```bash ... ``` ` or ` ```shell ... ``` `)**:
   - Use shell blocks for git operations, package installations (`pip`, `npm`), service queries, and diagnostics.
   - Example:
     ```bash
     git status -s
     ```

3. **Step-by-Step Execution**:
   - Prefer focused, sequential code actions rather than giant monolithic scripts.
   - Observe stdout/stderr from each step before deciding the next action.
   - When the objective is achieved, provide a clear, structured summary of what was accomplished.
"""


def load_soul(custom_path: Optional[Path] = None) -> str:
    """
    Loads custom SOUL.md from project or home directory, or falls back to DEFAULT_SOUL.
    Resolution Order:
    1. Explicit custom_path
    2. Active workspace .aja/SOUL.md
    3. Global ~/.aja/SOUL.md
    4. Built-in DEFAULT_SOUL
    """
    if custom_path and custom_path.exists():
        try:
            return custom_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("Failed to read custom SOUL.md at %s: %s", custom_path, e)

    ctx = get_current_workspace()
    if ctx:
        ws_soul = ctx.path / ".aja" / "SOUL.md"
        if ws_soul.exists():
            try:
                return ws_soul.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.debug("Failed reading workspace SOUL.md: %s", e)

    global_soul = Path.home() / ".aja" / "SOUL.md"
    if global_soul.exists():
        try:
            return global_soul.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.debug("Failed reading global SOUL.md: %s", e)

    return DEFAULT_SOUL.strip()


def load_project_guidelines(workspace_path: Optional[Path] = None) -> Optional[str]:
    """
    Inspects active workspace for project-specific instructions (AGENTS.md, CLAUDE.md, .cursorrules).
    """
    target_dir = workspace_path
    if not target_dir:
        ctx = get_current_workspace()
        if ctx:
            target_dir = ctx.path

    if not target_dir or not target_dir.exists():
        return None

    candidate_files = ["AGENTS.md", "CLAUDE.md", ".cursorrules", ".hermes.md"]
    for filename in candidate_files:
        candidate_path = target_dir / filename
        if candidate_path.is_file():
            try:
                content = candidate_path.read_text(encoding="utf-8").strip()
                if content:
                    return f"## Project Guidelines ({filename})\n{content}"
            except Exception as e:
                logger.debug("Failed reading project guideline %s: %s", candidate_path, e)
    return None


def build_system_prompt(
    goal: Optional[str] = None,
    specialist_name: Optional[str] = None,
    specialist_instructions: Optional[str] = None,
    semantic_summary: Optional[str] = None,
    past_episodes: Optional[List[Dict[str, Any]]] = None,
    available_skills: Optional[List[Any]] = None,
    custom_soul_path: Optional[Path] = None,
) -> str:
    """
    Assembles a modular, state-of-the-art tiered system prompt.
    """
    sections: List[str] = []

    # 1. Stable Tier: Persona & Identity
    soul = load_soul(custom_soul_path)
    sections.append(soul)

    # 2. Specialist Persona & Mission Role
    if specialist_name and specialist_instructions:
        specialist_block = f"""## Active Role: {specialist_name.upper()}
{specialist_instructions}
"""
        sections.append(specialist_block.strip())

    # 3. Action Space: CodeAct & Tool Calling Engine
    sections.append(CODEACT_GUIDANCE.strip())

    # 4. Workspace & Security Constraints
    ctx = get_current_workspace()
    ws_info_lines = ["## Active Workspace Context"]
    if ctx:
        ws_info_lines.append(f"- **Workspace ID**: `{ctx.id}` ({ctx.name})")
        ws_info_lines.append(f"- **Root Directory**: `{ctx.path}`")
        allow_oob = ctx.config_overrides.get("allow_out_of_bounds_paths", False)
        ws_info_lines.append(f"- **Sandbox Mode**: {'Unrestricted Host Mode' if allow_oob else 'Isolated Workspace Sandboxed'}")
    else:
        ws_info_lines.append(f"- **Mode**: Ambient Host Mode (`{Path.cwd()}`)")
    sections.append("\n".join(ws_info_lines))

    # 5. Project Guidelines (AGENTS.md / CLAUDE.md)
    guidelines = load_project_guidelines(ctx.path if ctx else None)
    if guidelines:
        sections.append(guidelines)

    # 6. CoALA Semantic Environment Facts
    if semantic_summary:
        sections.append(semantic_summary.strip())
    else:
        # Default minimal facts
        facts_summary = f"""## Host Environment Facts
- **OS**: `{platform.system()} {platform.release()}` ({platform.machine()})
- **Python**: `{platform.python_version()}` (`{sys.executable}`)
- **User**: `{os.environ.get('USER') or os.environ.get('USERNAME') or 'operator'}`
- **CPU Cores**: `{os.cpu_count() or 1}`
"""
        sections.append(facts_summary.strip())

    # 7. CoALA Procedural Skills
    if available_skills:
        skill_lines = ["## Available Procedural Skills (`~/.aja/skills/`)"]
        for sk in available_skills:
            name = getattr(sk, "name", str(sk))
            desc = getattr(sk, "description", "")
            skill_lines.append(f"- **`{name}`**: {desc}")
        sections.append("\n".join(skill_lines))

    # 8. CoALA Episodic Vector Recall (Lessons Learned)
    if past_episodes:
        episode_lines = ["## Relevant Past Experiences & Lessons Learned"]
        for ep in past_episodes:
            past_goal = ep.get("goal", "")
            refl = ep.get("reflection") or {}
            critique = refl.get("critique", "")
            lessons = ", ".join(refl.get("lessons_learned") or [])
            episode_lines.append(f"- **Past Goal**: {past_goal}")
            if critique:
                episode_lines.append(f"  * Critique: {critique}")
            if lessons:
                episode_lines.append(f"  * Lessons Learned: {lessons}")
        sections.append("\n".join(episode_lines))

    # 9. Current Task Objective
    if goal:
        task_block = f"""## Current Mission Objective
**{goal}**

Formulate actions as Python or Shell CodeAct blocks or native tool calls. Inspect before concluding.
"""
        sections.append(task_block.strip())

    return "\n\n---\n\n".join(sections)
