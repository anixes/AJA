from aja.config import DATA_DIR
import random
import json
import logging
from typing import List, Dict, Any

import aja.config
from aja.learning.strategy_store import strategy_store

logger = logging.getLogger(__name__)

EXPLORATION_STATE_FILE = aja.config.DATA_DIR / "agent_exploration_state.json"

class ExplorationController:
    def __init__(self):
        self.epsilon = 0.2
        self.strategy_usage: Dict[str, int] = {}
        self.total_usages = 0
        self.load_state()

    def load_state(self):
        if EXPLORATION_STATE_FILE.exists():
            try:
                with EXPLORATION_STATE_FILE.open("r", encoding="utf-8") as f:
                    state = json.load(f)
                self.epsilon = state.get("epsilon", 0.2)
                self.strategy_usage = state.get("strategy_usage", {})
                self.total_usages = sum(self.strategy_usage.values())
            except Exception:
                logger.exception("Failed to load exploration state from %s", EXPLORATION_STATE_FILE)

    def save_state(self):
        try:
            state = {
                "epsilon": self.epsilon,
                "strategy_usage": self.strategy_usage
            }
            EXPLORATION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with EXPLORATION_STATE_FILE.open("w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            logger.exception("Failed to save exploration state to %s", EXPLORATION_STATE_FILE)

    def load_from_mission(self, mission_id: str):
        try:
            from aja.runtime.mission_journal import MissionJournal
            journal = MissionJournal(mission_id)
            events = journal.read_events()
            explore_events = [e for e in events if e.get("event_type") == "EXPLORATION_STATE_UPDATED"]
            if explore_events:
                last_event = explore_events[-1]
                state = last_event.get("exploration_state", {})
                self.epsilon = state.get("epsilon", 0.2)
                self.strategy_usage = state.get("strategy_usage", {})
                self.total_usages = sum(self.strategy_usage.values())
        except Exception:
            logger.exception("Failed to load exploration state from mission journal %s", mission_id)

    def save_to_mission(self, mission_id: str):
        try:
            from aja.runtime.mission_journal import MissionJournal
            journal = MissionJournal(mission_id)
            journal.emit("EXPLORATION_STATE_UPDATED", {
                "exploration_state": {
                    "epsilon": self.epsilon,
                    "strategy_usage": self.strategy_usage
                }
            })
        except Exception:
            logger.exception("Failed to save exploration state to mission journal %s", mission_id)

    def track_usage(self, strategy_id: str):
        """
        Part D — Strategy Diversity Tracking
        """
        self.strategy_usage[strategy_id] = self.strategy_usage.get(strategy_id, 0) + 1
        self.total_usages += 1
        self.save_state()
        try:
            from aja.runtime.execution.activity import get_activity_context
            act_ctx = get_activity_context()
            run_id = act_ctx.run_id if (act_ctx and act_ctx.run_id) else "unmanaged"
            if run_id != "unmanaged":
                self.save_to_mission(run_id)
        except Exception as e:
            logger.debug("Failed to auto-propagate strategy usage to mission journal: %s", e)

    def update_epsilon(self, success: bool):
        """
        Part B — Adaptive Exploration
        """
        if success:
            self.epsilon = max(0.05, self.epsilon * 0.9) # Decrease epsilon
        else:
            self.epsilon = min(0.8, self.epsilon + 0.1) # Increase epsilon
        self.save_state()
        try:
            from aja.runtime.execution.activity import get_activity_context
            act_ctx = get_activity_context()
            run_id = act_ctx.run_id if (act_ctx and act_ctx.run_id) else "unmanaged"
            if run_id != "unmanaged":
                self.save_to_mission(run_id)
        except Exception as e:
            logger.debug("Failed to auto-propagate adaptive epsilon to mission journal: %s", e)

    def should_explore(self, is_sandbox: bool, risk_level: float, run_id: str = "unmanaged") -> bool:
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
        from aja.runtime.replay_guards import replay_safe_random
        if replay_safe_random(run_id, 0, "exploration") < self.epsilon:
            return True
            
        return False

exploration_controller = ExplorationController()
