import json
import logging
from pathlib import Path
from typing import Dict, Any, List
from agentx.config import PROJECT_ROOT
from agentx.orchestration.gateway import LLMGateway
from agentx.skills.skill_store import SkillStore

logger = logging.getLogger("agent.autonomy.reflection")

class ReflectionEngine:
    """
    Advanced Reflective Skill Synthesis.
    Analyzes completed tasks to extract reusable skills.
    """
    def __init__(self):
        self.gateway = LLMGateway()
        self.skill_store = SkillStore()
        self.baton_dir = PROJECT_ROOT / ".agentx" / "batons"

    async def reflect_on_completed_tasks(self):
        """Scan baton directory and synthesize skills from successful runs."""
        if not self.baton_dir.exists():
            return

        logger.info("Starting reflective skill synthesis...")
        for baton_path in self.baton_dir.glob("*.json"):
            try:
                with open(baton_path, "r") as f:
                    baton = json.load(f)
                
                if baton.get("status") == "completed" and not baton.get("reflected"):
                    await self._synthesize_skill(baton)
                    # Mark as reflected to avoid redundant processing
                    baton["reflected"] = True
                    with open(baton_path, "w") as f:
                        json.dump(baton, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to reflect on {baton_path.name}: {e}")

    async def _synthesize_skill(self, baton: Dict[str, Any]):
        """Use LLM to extract a reusable skill from a task outcome."""
        objective = baton.get("task", "Unknown Task")
        output = baton.get("worker_stdout", "")
        history = baton.get("history", [])
        
        prompt = f"""
        Objective: {objective}
        Outcome: {output}
        Process History: {json.dumps(history)}

        Analyze this task. If it represents a reusable pattern, extract it as a Skill.
        A Skill should be a sequence of tool calls or a generalized strategy.
        Return ONLY a JSON object in this format:
        {{
          "is_reusable": true,
          "name": "concise-name",
          "description": "what this skill does",
          "input_pattern": "keywords or regex that trigger this skill",
          "tool_sequence": ["list", "of", "tools"],
          "risk_level": "LOW | MEDIUM | HIGH"
        }}
        If it's not a reusable pattern, return {{"is_reusable": false}}.
        """

        response_str = await self.gateway.chat(
            prompt, 
            system="You are the Agent Reflection Engine. Your goal is to build a high-quality skill library."
        )

        try:
            # Clean JSON from response
            if "```json" in response_str:
                response_str = response_str.split("```json")[1].split("```")[0]
            elif "```" in response_str:
                response_str = response_str.split("```")[1].split("```")[0]
            
            data = json.loads(response_str)
            if data.get("is_reusable"):
                logger.info(f"Synthesized new skill: {data['name']}")
                self.skill_store.save_skill({
                    "name": data["name"],
                    "description": data["description"],
                    "input_pattern": data["input_pattern"],
                    "tool_sequence": data["tool_sequence"],
                    "risk_level": data["risk_level"],
                    "tags": ["synthetic", "reflective"],
                    "metadata": {"source_baton": objective}
                })
        except Exception as e:
            logger.error(f"Failed to synthesize skill: {e}")

    async def consolidate_skills(self):
        """
        Recursive Reflection: Review existing skills and merge redundant ones.
        """
        skills = self.skill_store.list_skills()
        if len(skills) < 2:
            return

        logger.info("Starting recursive skill consolidation...")
        
        # We group skills by name similarity or overlap and ask LLM to merge
        # For now, we'll just batch them all and ask for a cleanup
        skills_summary = "\n".join([f"- {s['name']}: {s['description']}" for s in skills])
        
        prompt = f"""
        Here is the current Skill Library for Agent:
        {skills_summary}

        Identify any redundant or overlapping skills. 
        If two skills can be merged into a more general one, suggest the merger.
        Return ONLY a list of actions in JSON:
        {{
          "merges": [
            {{ "source": "skill-a", "target": "skill-b", "new_definition": {{ ... }} }}
          ],
          "deletions": ["skill-name"]
        }}
        """

        response_str = await self.gateway.chat(
            prompt,
            system="You are the Agent Consolidation Engine. Keep the library efficient and clean."
        )

        try:
            # Simple cleaning for now
            if "```json" in response_str:
                response_str = response_str.split("```json")[1].split("```")[0]
            data = json.loads(response_str)
            
            for merge in data.get("merges", []):
                source_name = merge.get("source")
                target_name = merge.get("target")
                new_def = merge.get("new_definition")
                
                if not source_name or not target_name or not new_def:
                    continue

                logger.info(f"Merging {source_name} into {target_name}")
                
                # Find skills in current list
                source_skill = next((s for s in skills if s["name"] == source_name), None)
                target_skill = next((s for s in skills if s["name"] == target_name), None)
                
                if target_skill:
                    self.skill_store.update_skill(target_skill["skill_id"], new_def)
                    if source_skill:
                        self.skill_store.delete_skill(source_skill["skill_id"])
            
            for deletion in data.get("deletions", []):
                logger.info(f"Deleting redundant skill: {deletion}")
                skill_to_del = next((s for s in skills if s["name"] == deletion), None)
                if skill_to_del:
                    self.skill_store.delete_skill(skill_to_del["skill_id"])
        except Exception as e:
            logger.error(f"Consolidation failed: {e}")

async def run_reflection():
    engine = ReflectionEngine()
    await engine.reflect_on_completed_tasks()
    # Consolidate every 5 tasks or so
    await engine.consolidate_skills()

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_reflection())
