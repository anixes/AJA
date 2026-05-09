import time
import threading
from typing import List, Dict, Any

from agentx.goals.goal_engine import goal_engine
from agentx.memory.experience_store import experience_store
from agentx.memory.failure_memory import failure_memory
from agentx.orchestration.router import execute_routed_node

INTENT_SOURCES = [
    "scheduled_tasks",
    "recent_failures",
    "user_patterns",
    "system_state_changes"
]

SAFE_GOALS = [
    "retry",
    "optimize",
    "monitor",
    "cleanup",
    "notify"
]

DANGEROUS_GOALS = [
    "delete_files",
    "system_modification",
    "external_actions"
]

class Intent:
    def __init__(self, objective: str, category: str, benefit: float = 0.5, risk: float = 0.1, cost: float = 0.1, confidence: float = 0.8):
        self.objective = objective
        self.category = category
        self.benefit = benefit
        self.risk = risk
        self.cost = cost
        self.confidence = confidence

class IntentEngine:
    def __init__(self):
        self.THRESHOLD = 0.5
        self.MAX_AUTONOMOUS_ACTIONS = 3
        self.MAX_AUTONOMOUS_TIME = 300 # 5 minutes
        self.autonomy_enabled = True
        
        self.intent_last_run: Dict[str, float] = {}
        self.intent_failures: Dict[str, int] = {}
        self.COOLDOWN = 3600 # 1 hour
        self._running = False
        self._thread = None
        self.total_count = 0
        self.success_count = 0
        self.recent_actions = []

    def generate_intents(self, state: Dict) -> List[Intent]:
        """
        Generate candidate intents based on system state and history.
        """
        candidates = [
            Intent("Check system health", "monitor", benefit=0.6, risk=0.05),
            Intent("Clean temp files", "cleanup", benefit=0.4, risk=0.1),
            Intent("Optimize database performance", "optimize", benefit=0.7, risk=0.2),
        ]
        
        # Check recent failures
        from agentx.memory.failure_memory import failure_memory
        if failure_memory.get_recent():
            candidates.append(Intent("Retry failed deployment", "retry", benefit=0.8, risk=0.3))
            
        return candidates

    def rank(self, intents: List[Intent]) -> List[Intent]:
        """
        Rank intents based on Reward = (Benefit * Confidence) - Cost - Risk.
        """
        def score(i: Intent):
            # Phase 26: RL-lite reward function
            return (i.benefit * i.confidence) - i.cost - i.risk
            
        return sorted(intents, key=score, reverse=True)

    def safe(self, intent: Intent) -> bool:
        """
        Safety check for autonomous intents.
        """
        if intent.risk > 0.5:
            return False
        if any(bad in intent.objective.lower() for bad in DANGEROUS_GOALS):
            return False
        return True
        self.recent_actions: List[str] = []
        self.success_count = 0
        self.total_count = 0
        
        self._running = False
        self._thread = None
        
    def generate_intents(self, state: Dict[str, Any]) -> List[Intent]:
        # Mocking intent generation based on state
        intents = []
        
        # 1. Recent Failures
        for goal in goal_engine.goals:
            if goal.status == "FAILED":
                intents.append(Intent(f"Retry failed task: {goal.objective}", "retry", benefit=0.8, risk=0.2))
                
        # 2. Maintenance
        intents.append(Intent("Optimize slow workflow", "optimize", benefit=0.6, risk=0.1, cost=0.3))
        intents.append(Intent("Check system health", "monitor", benefit=0.9, risk=0.05, cost=0.1))
        intents.append(Intent("Clean temp files", "cleanup", benefit=0.4, risk=0.1, cost=0.2))
        
        return intents
        
    def score_intent(self, intent: Intent) -> float:
        # benefit - risk - cost + confidence
        
        # Apply learning rule:
        failures = self.intent_failures.get(intent.objective, 0)
        penalty = failures * 0.2
        
        base_score = intent.benefit - intent.risk - intent.cost + intent.confidence
        return base_score - penalty
        
    def rank(self, intents: List[Intent]) -> List[Intent]:
        scored = [(self.score_intent(i), i) for i in intents]
        # Filter by threshold and SAFE_GOALS
        valid = [i for s, i in scored if s >= self.THRESHOLD and i.category in SAFE_GOALS and not any(d in i.objective.lower() for d in DANGEROUS_GOALS)]
        # Sort descending by score
        return sorted(valid, key=lambda i: self.score_intent(i), reverse=True)
        
    def safe(self, intent: Intent) -> bool:
        if intent.risk > 0.6:
            return False
        if intent.confidence < 0.6:
            return False
        # mock unknown state check
        unknown_state = False
        if unknown_state:
            return False
        return True
        
    def execute(self, intent: Intent):
        print(f"[IntentEngine] Executing autonomous intent: {intent.objective}")
        # Mark start time and last run
        self.intent_last_run[intent.objective] = time.time()
        
        # Add to goal engine to leverage execution and planning
        goal_id = goal_engine.add_goal(intent.objective, priority=2)
        
        # Execute synchronously for autonomy loop (mocking)
        # In reality, this would await goal completion.
        # We will mock a successful result for now.
        result = {"success": True}
        
        # Part F - Memory Feedback Loop
        experience_store.save(intent.objective, None, result, {})
        if not result["success"]:
            failure_memory.update(intent.objective, "root", result.get("error", "Failed"), {}, None)
            self.intent_failures[intent.objective] = self.intent_failures.get(intent.objective, 0) + 1
            
        # Part H - Telegram Reporting
        from agentx.scheduler.telegram import _send_telegram_report
        action_desc = intent.objective.lower()
        if "retry" in action_desc: action = "retried deployment"
        elif "clean" in action_desc: action = "cleaned temp files"
        elif "health" in action_desc: action = "checked system health"
        else: action = action_desc
        
        _send_telegram_report(f"I automatically:\n- {action}")
        
        # Tracking for Drift Control
        self.total_count += 1
        if result["success"]:
            self.success_count += 1
        self.recent_actions.append(intent.objective)
        if len(self.recent_actions) > 10:
            self.recent_actions.pop(0)

    def check_drift_control(self):
        # Part J - Drift Control
        if self.total_count >= 5:
            success_rate = self.success_count / self.total_count
            if success_rate < 0.8: # Drop of 20% from ideal 1.0
                print("[IntentEngine] Success rate dropped significantly. Disabling autonomy.")
                self.autonomy_enabled = False
                
        # Repeated actions
        if len(self.recent_actions) >= 5:
            if len(set(self.recent_actions[-5:])) == 1:
                print("[IntentEngine] Repeated actions detected. Stopping loop.")
                self.autonomy_enabled = False
                
    def loop(self):
        while self._running:
            if not self.autonomy_enabled:
                time.sleep(5)
                continue
                
            cycle_start = time.time()
            state = {"status": "idle"}
            
            intents = self.generate_intents(state)
            ranked = self.rank(intents)
            
            actions_taken = 0
            for intent in ranked:
                if actions_taken >= self.MAX_AUTONOMOUS_ACTIONS:
                    break
                    
                if time.time() - cycle_start > self.MAX_AUTONOMOUS_TIME:
                    print("[IntentEngine] Autonomous budget exceeded.")
                    break
                
                # Part K - Cooldown System
                last_run = self.intent_last_run.get(intent.objective, 0)
                if time.time() - last_run < self.COOLDOWN:
                    continue
                    
                if self.safe(intent):
                    self.execute(intent)
                    actions_taken += 1
                else:
                    print(f"[IntentEngine] Intent unsafe, escalating: {intent.objective}")
                    from agentx.scheduler.telegram import _send_telegram_report
                    _send_telegram_report(f"Proposed unsafe action required approval: {intent.objective}")
                    
            self.check_drift_control()
            time.sleep(10)
            
    def start(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self.loop, daemon=True)
            self._thread.start()
            
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

intent_engine = IntentEngine()
