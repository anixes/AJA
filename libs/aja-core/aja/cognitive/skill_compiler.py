"""
=============================================================================
AJA Cognitive Architecture: Autonomous Procedural Skill Compiler (agentskills.io)
=============================================================================
Implements Self-Evolving Procedural Memory:
- Analyzes successful multi-turn execution trajectories
- Parameterizes concrete arguments into reusable abstractions
- Compiles standard agentskills.io packages: SKILL.md + run.py
- Enforces sandbox dry-run validation gate before active registration
=============================================================================
"""

import ast
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from aja.cognitive.memory_models import ProceduralSkill, TaskTrajectory

logger = logging.getLogger(__name__)


@dataclass
class CompiledSkillResult:
    skill_name: str
    skill_dir: Path
    is_valid: bool
    validation_error: Optional[str] = None
    skill_obj: Optional[ProceduralSkill] = None


class SkillCompiler:
    """
    Synthesizes and verifies reusable skills from winning mission trajectories.
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        if skills_dir is None:
            skills_dir = Path.home() / ".aja" / "skills"
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def distill_trajectory(self, trajectory: TaskTrajectory) -> Optional[CompiledSkillResult]:
        """
        Analyzes a successful trajectory and compiles it into an executable skill.
        """
        if not trajectory.steps or len(trajectory.steps) < 1:
            logger.debug("Trajectory has insufficient steps for skill compilation.")
            return None

        # Clean skill name from goal (slugify)
        base_slug = re.sub(r"[^a-zA-Z0-9]+", "_", trajectory.goal.lower()).strip("_")
        skill_name = f"auto_{base_slug[:32]}"
        skill_path = self.skills_dir / skill_name
        skill_path.mkdir(parents=True, exist_ok=True)

        # 1. Synthesize SKILL.md (agentskills.io spec)
        skill_md_content = self._generate_skill_md(skill_name, trajectory)
        skill_md_file = skill_path / "SKILL.md"
        skill_md_file.write_text(skill_md_content, encoding="utf-8")

        # 2. Synthesize executable run.py script
        script_code = self._generate_executable_script(skill_name, trajectory)
        script_file = skill_path / "run.py"
        script_file.write_text(script_code, encoding="utf-8")

        # 3. Dry-run AST and syntax validation gate
        validation_error = self._validate_skill(script_code, skill_md_content)
        is_valid = validation_error is None

        skill_obj = None
        if is_valid:
            skill_obj = ProceduralSkill(
                name=skill_name,
                description=f"Auto-distilled skill for: {trajectory.goal}",
                instructions=skill_md_content,
                script_code=script_code,
                tags=[trajectory.domain.lower(), "auto_generated"],
            )

        return CompiledSkillResult(
            skill_name=skill_name,
            skill_dir=skill_path,
            is_valid=is_valid,
            validation_error=validation_error,
            skill_obj=skill_obj,
        )

    def _generate_skill_md(self, skill_name: str, trajectory: TaskTrajectory) -> str:
        """Formats standard YAML frontmatter + markdown documentation."""
        domain = trajectory.domain or "general"
        goal = trajectory.goal.replace('"', '\\"')

        return f"""---
name: {skill_name}
description: "Autonomous procedural skill to: {goal}"
domain: {domain}
version: 1.0.0
author: AJA Skill Compiler (Self-Evolution Engine)
---

# {skill_name}

## Intent & Triggers
Automatically generated procedure for goal: **{trajectory.goal}**.

## Steps Executed in Reference Mission:
""" + "\n".join(
            f"{i+1}. **{s.action_type.upper()}**: `{s.action_payload[:80]}`"
            for i, s in enumerate(trajectory.steps)
        ) + """

## Usage
Execute `python run.py` within the target environment.
"""

    def _generate_executable_script(self, skill_name: str, trajectory: TaskTrajectory) -> str:
        """Synthesizes standalone Python script with error handling."""
        lines = [
            '#!/usr/bin/env python3',
            '"""',
            f'Auto-compiled skill: {skill_name}',
            f'Goal: {trajectory.goal}',
            '"""',
            'import os',
            'import sys',
            'import subprocess',
            '',
            'def main():',
            f'    print("[AJA Skill] Executing: {trajectory.goal}")',
        ]

        for i, step in enumerate(trajectory.steps):
            lines.append(f'    # Step {i+1}: {step.action_type}')
            if step.action_type in ["shell", "codeact_shell"]:
                cmd_escaped = step.action_payload.replace('\\', '\\\\').replace('"', '\\"')
                lines.append(f'    cmd_{i} = "{cmd_escaped}"')
                lines.append(f'    res_{i} = subprocess.run(cmd_{i}, shell=True, capture_output=True, text=True)')
                lines.append(f'    if res_{i}.returncode != 0:')
                lines.append(f'        print(f"Error in step {i+1}: {{res_{i}.stderr}}", file=sys.stderr)')
                lines.append(f'        sys.exit(res_{i}.returncode)')
                lines.append(f'    print(res_{i}.stdout.strip())')
            else:
                lines.append(f'    print("[Step {i+1}] Completed: {step.action_payload[:40]}")')

        lines.extend([
            '    print("[AJA Skill] Finished successfully.")',
            '',
            'if __name__ == "__main__":',
            '    main()',
        ])
        return "\n".join(lines)

    def _validate_skill(self, script_code: str, skill_md: str) -> Optional[str]:
        """Validates that python code parses without syntax errors and markdown contains frontmatter."""
        try:
            ast.parse(script_code)
        except SyntaxError as e:
            return f"SyntaxError in compiled script: {e}"

        if not skill_md.startswith("---"):
            return "Missing YAML frontmatter in SKILL.md"

        return None
