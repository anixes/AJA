import json
import os
from typing import Dict, Any, List

import agentx.config
from agentx.llm import get_gateway_for_model
from agentx.runtime.event_bus import bus, EVENTS

KNOWLEDGE_BASE_FILE = "d:/AgenticAI/Project1(no-name)/agentx_knowledge_base.json"

class KnowledgeBase:
    def __init__(self):
        self.problems = []
        self.solutions = []
        self.best_patterns = []
        self.patterns_freq = {}
        self.load()

    def add_pattern(self, pattern: Dict[str, Any]):
        p_id = pattern.get("pattern")
        if p_id not in self.patterns_freq:
            self.patterns_freq[p_id] = 1
            self.best_patterns.append(pattern)
        else:
            self.patterns_freq[p_id] += 1
            
        self.save()
        
        # Part C - Auto Tool Creation (Controlled)
        if self.patterns_freq[p_id] == 3: # Threshold
            print(f"[SelfEvolve] Pattern '{p_id}' crossed threshold. Proposing capability.")
            from agentx.self_build.capability_builder import self_build_cycle
            # Trigger self-build using the pattern description as the problem
            self_build_cycle(f"Automate workflow: {p_id}")

    def add_reflection(self, problem: str, reflection: Dict[str, Any]):
        self.problems.append(problem)
        self.solutions.append(reflection)
        self.save()

    def load(self):
        if os.path.exists(KNOWLEDGE_BASE_FILE):
            try:
                with open(KNOWLEDGE_BASE_FILE, "r") as f:
                    data = json.load(f)
                    self.problems = data.get("problems", [])
                    self.solutions = data.get("solutions", [])
                    self.best_patterns = data.get("best_patterns", [])
                    self.patterns_freq = data.get("patterns_freq", {})
            except Exception:
                pass

    def save(self):
        try:
            with open(KNOWLEDGE_BASE_FILE, "w") as f:
                json.dump({
                    "problems": self.problems,
                    "solutions": self.solutions,
                    "best_patterns": self.best_patterns,
                    "patterns_freq": self.patterns_freq
                }, f, indent=2)
        except Exception:
            pass

knowledge_base = KnowledgeBase()

def reflect(goal: str, plan: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Part A - Reflection Engine
    """
    model_name = agentx.config.AGENTX_PLANNER_MODEL
    gw, mapped_model = get_gateway_for_model(model_name)
    
    # Serialize plan for LLM
    plan_desc = ""
    if hasattr(plan, "nodes"):
        plan_desc = ", ".join([getattr(n, "task", "step") for n in plan.nodes])
    elif isinstance(plan, list):
        plan_desc = ", ".join([str(n) for n in plan])
    else:
        plan_desc = str(plan)
        
    system = """You are the AgentX Reflection Engine.
Analyze the executed goal, the plan, and its result.
Return ONLY JSON:
{
    "success": true/false,
    "what_worked": "...",
    "what_failed": "...",
    "bottlenecks": "...",
    "optimization_opportunities": "..."
}
"""
    prompt = f"Goal: {goal}\nPlan: {plan_desc}\nResult: {json.dumps(result)}\n\nReflect on this execution:"
    try:
        raw = gw.chat(model=mapped_model, prompt=prompt, system=system)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[Reflection] Error: {e}")
        return {
            "success": result.get("success", False),
            "what_worked": "",
            "what_failed": str(e),
            "bottlenecks": "",
            "optimization_opportunities": ""
        }

def extract_pattern(goal: str, plan: Any) -> Dict[str, Any]:
    """
    Part B - Pattern Extraction
    """
    plan_desc = []
    if hasattr(plan, "nodes"):
        plan_desc = [getattr(n, "task", "step") for n in plan.nodes]
    elif isinstance(plan, list):
        plan_desc = [str(n) for n in plan]
        
    return {
        "pattern": f"workflow_for_{goal.replace(' ', '_')[:20]}",
        "steps": plan_desc,
        "tools": [] # Extracted tools could go here
    }

def process_execution(goal: str, plan: Any, result: Dict[str, Any]):
    """
    Part F & H - Improvement Trigger & Feedback Loop
    Run after each execution
    """
    print(f"[SelfEvolve] Running reflection and pattern extraction for: {goal}")
    
    # Reflect
    reflection = reflect(goal, plan, result)
    
    # Extract Pattern
    pattern = extract_pattern(goal, plan)
    
    # Store Knowledge
    knowledge_base.add_reflection(goal, reflection)
    knowledge_base.add_pattern(pattern)
    
    # Part D - Self-Optimization
    from agentx.rl.policy_store import policy_store
    
    if reflection.get("bottlenecks"):
        print(f"[SelfEvolve] Identified bottlenecks: {reflection['bottlenecks']}")
        # adjust latency / scoring
        # example logic to simulate optimization
        pass
        
    if not reflection.get("success", True):
        print("[SelfEvolve] Failure detected in reflection. Triggering policy adjustment.")
        policy_store.exploration_rate = min(1.0, policy_store.exploration_rate + 0.1)

