import random
import json
import os
from typing import List, Dict, Any

from agentx.learning.strategy_store import strategy_store

EXPLORATION_STATE_FILE = "d:/AgenticAI/Project1(no-name)/agentx_exploration_state.json"

class ExplorationController:
    def __init__(self):
        self.epsilon = 0.2
        self.strategy_usage: Dict[str, int] = {}
        self.total_usages = 0
        self.load_state()

    def load_state(self):
        if os.path.exists(EXPLORATION_STATE_FILE):
            try:
                with open(EXPLORATION_STATE_FILE, "r") as f:
                    state = json.load(f)
                self.epsilon = state.get("epsilon", 0.2)
                self.strategy_usage = state.get("strategy_usage", {})
                self.total_usages = sum(self.strategy_usage.values())
            except Exception:
                pass

    def save_state(self):
        try:
            state = {
                "epsilon": self.epsilon,
                "strategy_usage": self.strategy_usage
            }
            with open(EXPLORATION_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    def track_usage(self, strategy_id: str):
        """
        Part D — Strategy Diversity Tracking
        """
        self.strategy_usage[strategy_id] = self.strategy_usage.get(strategy_id, 0) + 1
        self.total_usages += 1
        self.save_state()

    def update_epsilon(self, success: bool):
        """
        Part B — Adaptive Exploration
        """
        if success:
            self.epsilon = max(0.05, self.epsilon * 0.9) # Decrease epsilon
        else:
            self.epsilon = min(0.8, self.epsilon + 0.1) # Increase epsilon
        self.save_state()

    def should_explore(self, is_sandbox: bool, risk_level: float) -> bool:
        """
        Part A — Epsilon-Greedy Strategy Selection
        Part E — Safe Exploration
        Part C — Forced Exploration
        """
        # Part E - Safe exploration only
        if not is_sandbox and risk_level > 0.5:
            return False

        # Part C - Forced exploration if a strategy dominates
        if self.total_usages > 10:
            for strat, count in self.strategy_usage.items():
                if count / self.total_usages > 0.8:
                    print("[Exploration] Top strategy dominates >80%. Forcing exploration.")
                    return True

        # Part D - Strategy Diversity Tracking
        if len(self.strategy_usage) < 3 and self.total_usages > 10:
            print("[Exploration] Low strategy diversity. Forcing exploration.")
            return True

        # Part A - Epsilon-greedy
        if random.random() < self.epsilon:
            return True
            
        return False

exploration_controller = ExplorationController()
