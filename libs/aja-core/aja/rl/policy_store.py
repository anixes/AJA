import json
import os
import random

import aja.config

POLICY_FILE = aja.config.PROJECT_ROOT / ".aja" / "agent_policy.json"

class PolicyStore:
    def __init__(self):
        self.pattern_scores = {}
        self.tool_scores = {}
        self.mode_scores = {}
        
        self.exploration_rate = 0.2
        self.total_rewards = 0.0
        self.total_updates = 0
        self.avg_reward = 0.0
        
        self.load_policy()

    def compute_reward(self, result, latency: float, rollbacks: int, repairs: int) -> float:
        success_val = 1.0 if result.get("success", False) else -1.0
        reward = success_val - (0.2 * latency) - (0.5 * rollbacks) - (0.5 * repairs)
        return reward

    def extract_patterns(self, plan) -> list:
        # Simple placeholder for extracting patterns (node dependencies, sequences)
        patterns = []
        if hasattr(plan, "nodes"):
            for n in plan.nodes:
                patterns.append(getattr(n, "tool", "unknown_tool") + "_pattern")
        return patterns

    def update_policy(self, plan, result, latency: float = 0.1, rollbacks: int = 0, repairs: int = 0):
        # Part I - Safety Constraints
        if getattr(plan, "risk", 0.0) > 0.7:
            print("[RL] Unsafe behavior detected. Ignoring reward.")
            return

        reward = self.compute_reward(result, latency, rollbacks, repairs)
        
        for pattern in self.extract_patterns(plan):
            self.pattern_scores[pattern] = self.pattern_scores.get(pattern, 0.0) + 0.1 * reward

        tools = []
        if hasattr(plan, "nodes"):
            tools = [getattr(n, "tool", "unknown") for n in plan.nodes]
        for tool in tools:
            self.tool_scores[tool] = self.tool_scores.get(tool, 0.0) + 0.1 * reward

        mode = getattr(plan, "mode", "default")
        self.mode_scores[mode] = self.mode_scores.get(mode, 0.0) + 0.1 * reward
        
        self.total_rewards += reward
        self.total_updates += 1
        self.avg_reward = self.total_rewards / self.total_updates

        # Part H - Decay System
        self.decay_policy()
        self.save_policy()

    def decay_policy(self):
        decay_rate = 0.99
        self.pattern_scores = {k: v * decay_rate for k, v in self.pattern_scores.items()}
        self.tool_scores = {k: v * decay_rate for k, v in self.tool_scores.items()}
        self.mode_scores = {k: v * decay_rate for k, v in self.mode_scores.items()}

    def reset_policy(self):
        print("[RL] Success rate dropped significantly. Resetting policy.")
        self.pattern_scores = {}
        self.tool_scores = {}
        self.mode_scores = {}
        self.exploration_rate = 0.5  # increase exploration after reset
        self.save_policy()

    def save_policy(self):
        data = {
            "pattern_scores": self.pattern_scores,
            "tool_scores": self.tool_scores,
            "mode_scores": self.mode_scores,
            "avg_reward": self.avg_reward,
            "exploration_rate": self.exploration_rate
        }
        try:
            with open(POLICY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def load_policy(self):
        if os.path.exists(POLICY_FILE):
            try:
                with open(POLICY_FILE, "r") as f:
                    data = json.load(f)
                self.pattern_scores = data.get("pattern_scores", {})
                self.tool_scores = data.get("tool_scores", {})
                self.mode_scores = data.get("mode_scores", {})
                self.avg_reward = data.get("avg_reward", 0.0)
                self.exploration_rate = data.get("exploration_rate", 0.2)
            except Exception:
                pass

    def get_plan_bonus(self, plan) -> float:
        bonus = sum([self.pattern_scores.get(p, 0.0) for p in self.extract_patterns(plan)])
        tools = []
        if hasattr(plan, "nodes"):
            tools = [getattr(n, "tool", "unknown") for n in plan.nodes]
        bonus += sum([self.tool_scores.get(t, 0.0) for t in tools])
        
        mode = getattr(plan, "mode", "default")
        bonus += self.mode_scores.get(mode, 0.0)
        
        return bonus

policy_store = PolicyStore()
