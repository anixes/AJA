import json
import random
import time
import uuid
from typing import Dict, Any

from agentx.llm import get_gateway_for_model
import agentx.config

class CurriculumManager:
    def __init__(self):
        self.difficulty_level = 1.0
        self.successes_at_current_level = 0
        self.failures_at_current_level = 0
        self.last_training_time = 0
        self.training_frequency_modifier = 1.0

    def detect_skill_gap(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Part A — Skill Gap Detection
        """
        if result.get("success", True):
            # No major gaps if successful, but could check latency
            return {}
            
        model_name = agentx.config.AGENTX_PLANNER_MODEL
        gw, mapped_model = get_gateway_for_model(model_name)
        
        system = """You are AgentX Skill Gap Detector.
Analyze the failed execution result and detect the core weaknesses.
Return JSON ONLY:
{
    "failed_reasoning": "What logical leap failed",
    "weak_tool_usage": "Which tool was misused",
    "slow_execution": "Where did it stall",
    "uncertain_decisions": "What choice was incorrect"
}"""
        try:
            prompt = f"Failed Result: {json.dumps(result)}"
            raw = gw.chat(model=mapped_model, prompt=prompt, system=system)
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            return json.loads(raw)
        except Exception as e:
            print(f"[SkillGapDetector] Error: {e}")
            return {
                "failed_reasoning": "Unknown logic failure",
                "weak_tool_usage": result.get("error", "Unknown error")
            }

    def generate_training_task(self, skill_gap: Dict[str, Any]) -> Dict[str, Any]:
        """
        Part B — Task Generator
        """
        model_name = agentx.config.AGENTX_PLANNER_MODEL
        gw, mapped_model = get_gateway_for_model(model_name)
        
        system = f"""You are AgentX Curriculum Generator.
Based on the provided skill gaps and current difficulty level ({self.difficulty_level}), generate a synthetic training task for the agent.
It must be a safe, simulated task that allows the agent to practice the weak areas.
Return JSON ONLY:
{{
    "goal": "Descriptive task goal to practice",
    "difficulty": "Why this matches difficulty {self.difficulty_level}",
    "focus": "The specific gap being addressed"
}}"""
        prompt = f"Skill Gaps: {json.dumps(skill_gap)}"
        try:
            raw = gw.chat(model=mapped_model, prompt=prompt, system=system)
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            task = json.loads(raw)
            return task
        except Exception:
            return {
                "goal": "Write a python script that handles errors robustly",
                "difficulty": "Baseline practice",
                "focus": "Error handling practice"
            }

    def evaluate_training_result(self, result: Dict[str, Any]):
        """
        Part D — Difficulty Curriculum
        Part H — Stop Condition
        """
        if result.get("success", False):
            self.successes_at_current_level += 1
        else:
            self.failures_at_current_level += 1

        total = self.successes_at_current_level + self.failures_at_current_level
        if total >= 3:
            success_rate = self.successes_at_current_level / total
            if success_rate > 0.8:
                self.difficulty_level += 0.5
                print(f"[Curriculum] Agent mastered level. Increasing difficulty to {self.difficulty_level}")
                self.successes_at_current_level = 0
                self.failures_at_current_level = 0
                
                # Part H - Stop Condition
                if self.difficulty_level > 5.0:
                    print("[Curriculum] System performance stable. Reducing training frequency.")
                    self.training_frequency_modifier = 0.2
                    
            elif success_rate < 0.5:
                self.difficulty_level = max(1.0, self.difficulty_level - 0.5)
                print(f"[Curriculum] Agent struggling. Reducing difficulty to {self.difficulty_level}")
                self.successes_at_current_level = 0
                self.failures_at_current_level = 0

    def should_train(self) -> bool:
        now = time.time()
        # Default every 5 minutes if idle, scaled by modifier
        interval = 300 / max(0.1, self.training_frequency_modifier)
        if now - self.last_training_time > interval:
            return True
        return False
        
    def mark_training_started(self):
        self.last_training_time = time.time()

curriculum_manager = CurriculumManager()
