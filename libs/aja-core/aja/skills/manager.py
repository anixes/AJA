import os
import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from aja.capabilities.base import Capability, CapabilityResult
from aja.capabilities.registry import registry

logger = logging.getLogger(__name__)

class Skill(Capability):
    """
    A procedural skill composed of multiple capability calls.
    """
    def __init__(self, name: str, description: str, steps: List[Dict[str, Any]]):
        self.name = f"skill.{name}"
        self.description = description
        self.steps = steps
        self.input_schema = {
            "context": "dict (optional context for steps)"
        }

    def execute(self, inputs: dict) -> CapabilityResult:
        context = inputs.get("context", {})
        results = []
        
        for i, step in enumerate(self.steps):
            cap_name = step.get("capability") or step.get("tool_name")
            cap_inputs = step.get("inputs") or step.get("arguments", {})
            
            # Resolve template substitution in inputs
            resolved_inputs = self._resolve_templates(cap_inputs, context, results)
            
            try:
                cap = registry.get(cap_name)
                res = cap.execute(resolved_inputs)
                
                step_result = {
                    "capability": cap_name,
                    "success": res.success,
                    "output": res.output,
                    "error": res.error
                }
                results.append(step_result)
                
                if not res.success:
                    return CapabilityResult(
                        success=False,
                        output={"steps_completed": results},
                        error=f"Step {i} ('{cap_name}') failed: {res.error}"
                    )
            except Exception as e:
                return CapabilityResult(
                    success=False,
                    output={"steps_completed": results},
                    error=f"Execution error at step {i} ('{cap_name}'): {str(e)}"
                )
                
        return CapabilityResult(
            success=True,
            output={"results": results}
        )

    def _resolve_templates(self, inputs: Any, context: dict, results: list) -> Any:
        """
        Recursively resolve templates like {{context.key}} or {{step_0.output.field}}
        """
        if isinstance(inputs, str):
            # Resolve {{context.VAR}}
            def replace_context(match):
                key = match.group(1)
                return str(context.get(key, match.group(0)))
            
            # Resolve {{step_N.output.VAR}}
            def replace_step(match):
                idx = int(match.group(1))
                field = match.group(2)
                if idx < len(results):
                    out = results[idx].get("output", {})
                    if isinstance(out, dict):
                        return str(out.get(field, match.group(0)))
                    return str(out)
                return match.group(0)

            val = re.sub(r'\{\{context\.([^}]+)\}\}', replace_context, inputs)
            val = re.sub(r'\{\{step_(\d+)\.output\.([^}]+)\}\}', replace_step, val)
            return val
        
        elif isinstance(inputs, dict):
            return {k: self._resolve_templates(v, context, results) for k, v in inputs.items()}
        elif isinstance(inputs, list):
            return [self._resolve_templates(v, context, results) for v in inputs]
        
        return inputs

class SkillManager:
    """
    Manages procedural skills for Agent.
    """
    def __init__(self, skills_dir: Optional[Path] = None):
        if skills_dir is None:
            # Default to .aja/skills in the project root
            self.skills_dir = Path(".aja/skills")
        else:
            self.skills_dir = skills_dir
            
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def load_all(self):
        """Loads all .json skills from the skills directory."""
        if not self.skills_dir.exists():
            return

        for skill_file in self.skills_dir.glob("*.json"):
            try:
                with open(skill_file, 'r') as f:
                    data = json.load(f)
                    skill = Skill(
                        name=data["name"],
                        description=data["description"],
                        steps=data["steps"]
                    )
                    registry.register(skill)
                    logger.info(f"Registered skill: {skill.name}")
            except Exception as e:
                logger.error(f"Failed to load skill from {skill_file}: {e}")

    def create_skill(self, name: str, description: str, steps: List[Dict[str, Any]]):
        """Creates and saves a new skill."""
        data = {
            "name": name,
            "description": description,
            "steps": steps
        }
        
        skill_path = self.skills_dir / f"{name}.json"
        with open(skill_path, 'w') as f:
            json.dump(data, f, indent=2)
            
        # Register immediately
        skill = Skill(name, description, steps)
        registry.register(skill)
        return skill_path
