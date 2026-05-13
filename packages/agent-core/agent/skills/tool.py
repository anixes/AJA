from agent.capabilities.base import Capability, CapabilityResult
from .manager import SkillManager
from typing import Any

class SkillManageTool(Capability):
    """
    Capability for the agent to manage its own procedural skills.
    Allows for creating, listing, and deleting persistent skill sequences.
    """
    name = "skill.manage"
    input_schema = {
        "action": "str (create | list | delete)",
        "name": "str (required for create/delete)",
        "description": "str (required for create)",
        "steps": "list[dict] (required for create)"
    }

    def __init__(self):
        self.manager = SkillManager()

    def execute(self, inputs: dict) -> CapabilityResult:
        action = inputs.get("action")
        name = inputs.get("name")
        
        if action == "create":
            description = inputs.get("description")
            steps = inputs.get("steps")
            if not all([name, description, steps]):
                return CapabilityResult(success=False, output={}, error="Missing required fields for 'create'")
            
            path = self.manager.create_skill(name, description, steps)
            return CapabilityResult(success=True, output={"path": str(path), "msg": f"Skill '{name}' created and registered."})
            
        elif action == "list":
            skills = [p.stem for p in self.manager.skills_dir.glob("*.json")]
            return CapabilityResult(success=True, output={"procedural_skills": skills})

        elif action == "search":
            # Search existing captured skills in the SkillStore (LanceDB/Arrow)
            from agent.skills.skill_store import search_skills
            query = inputs.get("query", "")
            results = search_skills(query)
            return CapabilityResult(success=True, output={"captured_skills": results})

        elif action == "promote":
            # Promote a captured skill to a procedural skill
            from agent.skills.skill_store import get_skill
            skill_id = inputs.get("skill_id")
            if not skill_id:
                return CapabilityResult(success=False, output={}, error="Missing 'skill_id' for 'promote'")
            
            stored_skill = get_skill(skill_id)
            if not stored_skill:
                return CapabilityResult(success=False, output={}, error=f"Skill ID '{skill_id}' not found in store.")
            
            name = stored_skill["name"].lower().replace(" ", "_")
            steps = stored_skill.get("tool_sequence") # This is a JSON string in DB
            if isinstance(steps, str):
                import json
                steps = json.loads(steps)
                
            path = self.manager.create_skill(name, stored_skill["description"], steps)
            return CapabilityResult(success=True, output={"path": str(path), "msg": f"Skill promoted to procedural: '{name}'"})
            
        elif action == "delete":
            if not name:
                return CapabilityResult(success=False, output={}, error="Missing 'name' for 'delete'")
            path = self.manager.skills_dir / f"{name}.json"
            if path.exists():
                path.unlink()
                return CapabilityResult(success=True, output={"msg": f"Skill '{name}' deleted."})
            return CapabilityResult(success=False, output={}, error=f"Skill '{name}' not found.")
            
        return CapabilityResult(success=False, output={}, error=f"Unknown action: {action}")
