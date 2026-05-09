import json
import os
import uuid
import re
from typing import Dict, Any, List

import agentx.config
from agentx.llm import get_gateway_for_model

STRATEGY_FILE = "d:/AgenticAI/Project1(no-name)/agentx_strategies.json"

import time

class StrategyStore:
    def __init__(self, max_capacity: int = 50):
        self.strategies: List[Dict[str, Any]] = []
        self.max_capacity = max_capacity
        self.load()

    def score_experience(self, exp: Dict[str, Any]) -> float:
        """
        Part A — Experience Scoring
        """
        success_weight = 0.5
        latency_weight = 0.2
        stability_weight = 0.3
        
        success = exp.get("success_rate", 1.0)
        # Prevent division by zero, max latency score 1.0
        latency = min(1.0, 1.0 / max(exp.get("latency_avg", 1.0), 0.1))
        
        executions = exp.get("executions", 1)
        failures = exp.get("failures", 0)
        consistency = 1.0 - (failures / executions) if executions > 0 else 1.0
        
        return (success_weight * success) + (latency_weight * latency) + (stability_weight * consistency)

    def add(self, strategy: Dict[str, Any]):
        strategy["id"] = str(uuid.uuid4())[:8]
        strategy["success_rate"] = 1.0
        strategy["executions"] = 1
        strategy["failures"] = 0
        strategy["weight"] = 1.0
        strategy["latency_avg"] = strategy.get("latency", 1.0)
        strategy["created_at"] = time.time()
        
        self.strategies.append(strategy)
        self.cleanup()
        self.save()
        print(f"[StrategyStore] Added new strategy: {strategy.get('strategy_type')}")

    def cleanup(self):
        """
        Part C — Top-K Memory
        """
        if len(self.strategies) > self.max_capacity:
            # Sort by score ascending (lowest first) to drop them
            self.strategies.sort(key=lambda x: self.score_experience(x))
            self.strategies = self.strategies[-self.max_capacity:]

    def get_trusted_strategies(self) -> List[Dict[str, Any]]:
        """
        Part G — Trusted Memory
        """
        return [s for s in self.strategies if self.score_experience(s) >= 0.7 and s["executions"] > 2]
        
    def get_experimental_strategies(self) -> List[Dict[str, Any]]:
        return [s for s in self.strategies if self.score_experience(s) < 0.7 or s["executions"] <= 2]

    def decay_old_knowledge(self):
        """
        Part E — Decay Old Knowledge
        """
        now = time.time()
        threshold = 86400 * 7 # 7 days
        for s in self.strategies:
            if now - s.get("created_at", now) > threshold:
                s["weight"] *= 0.9

    def search(self, goal: str, top_k: int = 3) -> List[Dict[str, Any]]:
        self.decay_old_knowledge()
        
        if not self.strategies:
            return []
            
        def match_score(s):
            # Keyword matching score
            words = set(re.findall(r'\w+', goal.lower()))
            s_words = set(re.findall(r'\w+', s.get("strategy_type", "").lower() + " " + " ".join(s.get("success_conditions", []))))
            m_score = len(words.intersection(s_words))
            return m_score * self.score_experience(s) * s.get("weight", 1.0)
            
        # Part D — Competition between strategies
        scored = [(s, match_score(s)) for s in self.strategies]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, sc in scored if sc > 0][:top_k]

    def update_strategy(self, strategy_id: str, success: bool, latency: float = 1.0, failed_step: str = None):
        from agentx.learning.exploration import exploration_controller
        exploration_controller.track_usage(strategy_id)
        exploration_controller.update_epsilon(success)
        
        for s in self.strategies:
            if s.get("id") == strategy_id:
                s["executions"] += 1
                # Update rolling average latency
                s["latency_avg"] = (s["latency_avg"] * (s["executions"] - 1) + latency) / s["executions"]
                
                if success:
                    s["success_rate"] = ((s["success_rate"] * (s["executions"] - 1)) + 1.0) / s["executions"]
                    s["weight"] += 0.1
                    # Part G — Discovery Bonus
                    if s["executions"] <= 2:
                        print(f"[StrategyStore] Discovery Bonus! New strategy {strategy_id} succeeded.")
                        s["weight"] += 0.3
                else:
                    # Part F — Failure Feedback Loop
                    s["failures"] += 1
                    s["success_rate"] = ((s["success_rate"] * (s["executions"] - 1))) / s["executions"]
                    s["weight"] -= 0.2 # Penalty
                    print(f"[StrategyStore] Marked strategy {strategy_id} as weak due to failure at: {failed_step}")
                
                # Part B — Filtering Bad Knowledge
                if s["success_rate"] < 0.6 and s["executions"] > 3:
                    print(f"[StrategyStore] Discarding bad strategy {strategy_id} (success rate: {s['success_rate']})")
                    self.strategies.remove(s)
                
                self.save()
                break

    def load(self):
        if os.path.exists(STRATEGY_FILE):
            try:
                with open(STRATEGY_FILE, "r") as f:
                    self.strategies = json.load(f)
            except Exception:
                pass

    def save(self):
        try:
            with open(STRATEGY_FILE, "w") as f:
                json.dump(self.strategies, f, indent=2)
        except Exception:
            pass

strategy_store = StrategyStore()

def extract_strategy(goal: str, plan: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Part A - Strategy Extraction
    Extracts 'when and why to do it'
    """
    model_name = agentx.config.AGENTX_PLANNER_MODEL
    gw, mapped_model = get_gateway_for_model(model_name)
    
    plan_desc = ""
    if hasattr(plan, "nodes"):
        plan_desc = ", ".join([getattr(n, "task", "step") for n in plan.nodes])
    elif isinstance(plan, list):
        plan_desc = ", ".join([str(n) for n in plan])
    else:
        plan_desc = str(plan)
        
    system = """You are AgentX Strategy Extractor.
Extract the strategy from the successful execution.
Focus on 'when and why' to do this, not just 'how'.

Return ONLY JSON:
{
    "strategy_type": "Brief descriptive name",
    "steps": ["High-level logical step 1", "..."],
    "decision_points": ["What conditional logic was needed?"],
    "tools_used": ["Tool classes/types used"],
    "success_conditions": ["When should this strategy be used?"]
}
"""
    prompt = f"Goal: {goal}\nPlan: {plan_desc}\nExecution Trace/Result: {json.dumps(result)}\n\nExtract strategy:"
    try:
        raw = gw.chat(model=mapped_model, prompt=prompt, system=system)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[StrategyExtractor] Error: {e}")
        return None

def process_strategy_learning(goal: str, plan: Any, result: Dict[str, Any]):
    """
    Part H - Feedback Loop
    """
    if result.get("success", False):
        strategy = extract_strategy(goal, plan, result)
        if strategy:
            # We add it if similar doesn't exist, else update
            sims = strategy_store.search(goal, top_k=1)
            if sims and sims[0]["success_rate"] > 0.5:
                # Update existing successful strategy weight
                strategy_store.update_strategy(sims[0]["id"], True, latency=1.0) # Using 1.0 as dummy latency
            else:
                strategy_store.add(strategy)
    else:
        # Part F - Failure Analysis
        failed_step = result.get("error", "Unknown step")
        sims = strategy_store.search(goal, top_k=1)
        if sims:
            strategy_store.update_strategy(sims[0]["id"], False, failed_step=failed_step)
