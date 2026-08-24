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

        # 1. Synthesize SKILL.md (agentskills.io spec)
        skill_md_content = self._generate_skill_md(skill_name, trajectory)

        # 2. Synthesize executable run.py script
        script_code = self._generate_executable_script(skill_name, trajectory)

        # 3. Dry-run AST and syntax validation gate (BEFORE any files are written)
        validation_error = self._validate_skill(script_code, skill_md_content)
        is_valid = validation_error is None

        # 4. Sandboxed dry-run trial: execute the script's TOP LEVEL only in an
        # isolated subprocess. The generated main() body is guarded behind
        # `if __name__ == "__main__"`, so no recorded shell commands are replayed;
        # this proves the module loads cleanly without side effects or injection.
        if is_valid:
            validation_error = self._sandbox_dry_run(script_code)
            is_valid = validation_error is None

        if not is_valid:
            logger.warning("Rejected compiled skill %r: %s", skill_name, validation_error)
            return CompiledSkillResult(
                skill_name=skill_name,
                skill_dir=skill_path,
                is_valid=False,
                validation_error=validation_error,
                skill_obj=None,
            )

        skill_path.mkdir(parents=True, exist_ok=True)
        skill_md_file = skill_path / "SKILL.md"
        skill_md_file.write_text(skill_md_content, encoding="utf-8")
        script_file = skill_path / "run.py"
        script_file.write_text(script_code, encoding="utf-8")

        skill_obj = ProceduralSkill(
            name=skill_name,
            description=f"Auto-distilled skill for: {trajectory.goal}",
            instructions=skill_md_content,
            script_code=script_code,
            tags=[(trajectory.domain or "general").lower(), "auto_generated"],
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
        goal = trajectory.goal
        safe_description = json.dumps(f"Autonomous procedural skill to: {goal}")
        safe_goal_inline = goal.replace("\n", " ").replace("`", "'")

        return f"""---
name: {skill_name}
description: {safe_description}
domain: {domain}
version: 1.0.0
author: AJA Skill Compiler (Self-Evolution Engine)
---

# {skill_name}

## Intent & Triggers
Automatically generated procedure for goal: **{safe_goal_inline}**.

## Steps Executed in Reference Mission:
""" + "\n".join(
            f"{i+1}. **{s.action_type.upper()}**: `{str(s.action_payload or '')[:80].replace(chr(10), ' ').replace(chr(96), chr(39))}`"
            for i, s in enumerate(trajectory.steps)
        ) + """

## Usage
Execute `python run.py` within the target environment.
"""

    def _generate_executable_script(self, skill_name: str, trajectory: TaskTrajectory) -> str:
        """Synthesizes standalone Python script with error handling."""
        goal_literal = repr(trajectory.goal)
        lines = [
            '#!/usr/bin/env python3',
            '"""',
            f'Auto-compiled skill: {skill_name}',
            '"""',
            'import os',
            'import shlex',
            'import sys',
            'import subprocess',
            '',
            f'GOAL = {goal_literal}',
            '',
            'def main():',
            '    print("[AJA Skill] Executing:", GOAL)',
        ]

        for i, step in enumerate(trajectory.steps):
            lines.append(f'    # Step {i+1}: {step.action_type}')
            if step.action_type in ["shell", "codeact_shell"]:
                cmd_literal = repr(step.action_payload)
                lines.append(f'    cmd_{i} = {cmd_literal}')
                lines.append(f'    res_{i} = subprocess.run(shlex.split(cmd_{i}) if os.name != "nt" else cmd_{i}, shell=(os.name == "nt"), capture_output=True, text=True)')
                lines.append(f'    if res_{i}.returncode != 0:')
                lines.append(f'        print(f"Error in step {{i + 1}}: {{res_{i}.stderr}}", file=sys.stderr)')
                lines.append(f'        sys.exit(res_{i}.returncode)')
                lines.append(f'    print(res_{i}.stdout.strip())')
            else:
                payload_literal = repr(str(step.action_payload or "")[:40])
                lines.append(f'    print("[Step {i+1}] Completed:", {payload_literal})')

        lines.extend([
            '    print("[AJA Skill] Finished successfully.")',
            '',
            'if __name__ == "__main__":',
            '    main()',
        ])
        return "\n".join(lines)

    _FORBIDDEN_MODULES = {"ctypes", "pickle", "socket", "http", "urllib"}
    _FORBIDDEN_CALLS = {"system", "popen", "exec", "eval", "evalframe"}

    def _validate_skill(self, script_code: str, skill_md: str) -> Optional[str]:
        """Validates syntax, frontmatter, and scans the AST for dangerous constructs."""
        try:
            tree = ast.parse(script_code)
        except SyntaxError as e:
            return f"SyntaxError in compiled script: {e}"

        if not skill_md.startswith("---"):
            return "Missing YAML frontmatter in SKILL.md"

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in self._FORBIDDEN_MODULES:
                        return f"Forbidden module import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in self._FORBIDDEN_MODULES:
                    return f"Forbidden module import: {node.module}"
            elif isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name in self._FORBIDDEN_CALLS:
                    return f"Forbidden call: {name}() at line {node.lineno}"

        return None

    def _sandbox_dry_run(self, script_code: str, timeout_s: float = 15.0) -> Optional[str]:
        """
        Executes the generated script's module top-level in an isolated subprocess.
        Returns None on success, or a validation error string on failure.

        Safety properties:
        - Runs in a throwaway subprocess (not in-process).
        - The skill body is only imported; `main()` is never invoked because
          generated scripts guard execution behind `if __name__ == "__main__"`.
        - Hard wall-clock timeout kills runaway imports.
        """
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory(prefix="aja_skill_dry_") as tmp:
            trial = Path(tmp) / "run.py"
            trial.write_text(script_code, encoding="utf-8")
            loader = (
                "import importlib.util; "
                f"spec = importlib.util.spec_from_file_location('aja_dry_skill', r'{trial}'); "
                "module = importlib.util.module_from_spec(spec); "
                "spec.loader.exec_module(module)"
            )
            try:
                completed = subprocess.run(
                    [sys.executable, "-c", loader],
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired:
                return f"Sandbox dry-run timed out after {timeout_s}s"
            except Exception as e:
                return f"Sandbox dry-run could not start: {e}"

            if completed.returncode != 0:
                stderr_tail = (completed.stderr or "")[-500:]
                return f"Sandbox dry-run failed (rc={completed.returncode}): {stderr_tail}"

        return None
