import time
import json
import uuid
import os
from typing import List, Dict, Any

from agent.planning.planner import Planner
from agent.orchestration.router import execute_routed_node
from agent.runtime.event_bus import bus, EVENTS

import agent.config

GLOBAL_STATE_FILE = agent.config.PROJECT_ROOT / ".agent" / "agent_state.json"

class Goal:
    def __init__(self, objective: str, priority: int, deadline: float = None, is_sandbox: bool = False):
        self.id = str(uuid.uuid4())[:8]
        self.objective = objective
        self.priority = priority
        self.deadline = deadline or float('inf')
        self.is_sandbox = is_sandbox
        self.subgoals = []
        self.status = "PENDING"
        
        self.progress = {
            "completed_steps": [],
            "failed_steps": [],
            "current_state": "Not started"
        }
        self.failures = 0
        self.retries = 0
        
    def to_dict(self):
        return {
            "id": self.id,
            "objective": self.objective,
            "priority": self.priority,
            "deadline": self.deadline,
            "is_sandbox": self.is_sandbox,
            "status": self.status,
            "progress": self.progress,
            "failures": self.failures,
            "retries": self.retries
        }

    @classmethod
    def from_dict(cls, data):
        g = cls(data["objective"], data["priority"], data.get("deadline"), data.get("is_sandbox", False))
        g.id = data["id"]
        g.status = data["status"]
        g.progress = data.get("progress", {"completed_steps": [], "failed_steps": [], "current_state": ""})
        g.failures = data.get("failures", 0)
        g.retries = data.get("retries", 0)
        return g

class GoalEngine:
    def __init__(self):
        self.goals: List[Goal] = []
        self.planner = Planner()
        try:
            from agent.self_evolve.reflection import knowledge_base
            self.planner.bias(knowledge_base)
        except Exception as e:
            print(f"[GoalEngine] Failed to load knowledge base into planner: {e}")
            
        self.autonomy_enabled = True
        self.is_interrupted = False
        self.max_retries = 3
        self.load_state()
        
    def add_goal(self, objective: str, priority: int = 1, deadline: float = None, is_sandbox: bool = False) -> str:
        g = Goal(objective, priority, deadline, is_sandbox)
        self.goals.append(g)
        self.save_state()
        return g.id
        
    def get_active_goals(self) -> List[Goal]:
        active = [g for g in self.goals if g.status not in ["DONE", "FAILED", "PAUSED"]]
        return self.prioritize(active)
        
    def prioritize(self, goals: List[Goal]) -> List[Goal]:
        # urgent + high priority -> first
        # Sort by priority descending, then deadline ascending
        return sorted(goals, key=lambda g: (-g.priority, g.deadline))
        
    def expand_goal(self, goal: Goal):
        try:
            from agent.learning.strategy_store import strategy_store
            similar_strategies = strategy_store.search(goal.objective)
            trusted = [s for s in similar_strategies if strategy_store.score_experience(s) >= 0.7 and s["executions"] > 2]
            experimental = [s for s in similar_strategies if s not in trusted]
            self.planner.bias_with_strategies(trusted, experimental, is_sandbox=goal.is_sandbox, risk_level=0.1)
        except Exception as e:
            print(f"[GoalEngine] Strategy Search error: {e}")
        return self.planner.decompose(goal.objective)
        
    def update_goal_state(self, goal: Goal, result: Any, node_id: str):
        success = getattr(result, "success", False) if not isinstance(result, dict) else result.get("success", False)
        if success:
            goal.progress["completed_steps"].append(node_id)
            goal.progress["current_state"] = f"Completed {node_id}"
            goal.retries = 0
        else:
            goal.progress["failed_steps"].append(node_id)
            goal.failures += 1
            goal.retries += 1
            error = getattr(result, "error", "") if not isinstance(result, dict) else result.get("error", "")
            goal.progress["current_state"] = f"Failed {node_id}: {error}"
            
        self.save_state()
        
    def loop_control_check(self, goal: Goal) -> bool:
        if goal.retries > self.max_retries:
            print(f"[GoalEngine] Goal {goal.id} exceeded max retries. Marking FAILED.")
            goal.status = "FAILED"
            self.escalate_to_user(f"Goal {goal.objective} repeatedly failed.")
            return False
        
        # Stability Check (Part L)
        total_recent_failures = sum(g.failures for g in self.goals[-5:])
        if total_recent_failures > 10:
            self.disable_autonomy()
            self.fallback_to_manual()
            return False
            
        return True
        
    def escalate_to_user(self, message: str):
        print(f"[ESCALATION] {message}")
        
    def disable_autonomy(self):
        print("[GoalEngine] System instability detected! Disabling autonomy.")
        self.autonomy_enabled = False
        
    def fallback_to_manual(self):
        print("[GoalEngine] Falling back to manual mode. User approval required for all actions.")
        
    def modify_goal_strategy(self, goal: Goal):
        print(f"[GoalEngine] Modifying strategy for goal {goal.objective} due to failures.")
        # Trigger Self-Build Cycle (Part D - Self-Improvement Loop)
        from agent.self_build.capability_builder import self_build_cycle
        self_build_cycle(goal.objective)
        
        goal.objective = f"fallback: {goal.objective}"
        goal.retries = 0
        
    def save_state(self):
        state = {
            "goals": [g.to_dict() for g in self.goals],
            "system_state": {
                "autonomy_enabled": self.autonomy_enabled,
                "is_interrupted": self.is_interrupted
            }
        }
        try:
            with open(GLOBAL_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass
            
    def load_state(self):
        if os.path.exists(GLOBAL_STATE_FILE):
            try:
                with open(GLOBAL_STATE_FILE, "r") as f:
                    state = json.load(f)
                self.goals = [Goal.from_dict(d) for d in state.get("goals", [])]
                sys_state = state.get("system_state", {})
                self.autonomy_enabled = sys_state.get("autonomy_enabled", True)
                self.is_interrupted = sys_state.get("is_interrupted", False)
            except Exception:
                pass

    def run_step(self):
        if self.is_interrupted:
            print("[GoalEngine] Execution paused due to interruption.")
            return
            
        if not self.autonomy_enabled:
            return
            
        active = self.get_active_goals()
        if not active:
            return
            
        goal = active[0]
        if not self.loop_control_check(goal):
            return
            
        if goal.failures > 2 and goal.retries > 0:
            self.modify_goal_strategy(goal)
            
        print(f"\n[GoalEngine] Executing next step for goal: {goal.objective}")
        try:
            plan = self.expand_goal(goal)
            
            # Emit PLAN_CREATED event with a quick summary
            plan_summary = f"Objective: {goal.objective}"
            if hasattr(plan, "nodes") and plan.nodes:
                plan_summary += f"\nSteps: {len(plan.nodes)}"
                for i, n in enumerate(plan.nodes[:3]):
                    plan_summary += f"\n  {i+1}. {getattr(n, 'task', 'step')}"
                if len(plan.nodes) > 3:
                    plan_summary += f"\n  ...and {len(plan.nodes)-3} more"
            
            bus.publish(EVENTS["PLAN_CREATED"], {"plan_summary": plan_summary})
            
            # Simple simulation of execution
            if hasattr(plan, "nodes") and plan.nodes:
                node = plan.nodes[0]
            elif isinstance(plan, list) and len(plan) > 0:
                node = plan[0]
            else:
                node = type("Node", (), {"id": "n1", "risk": 0.5, "tool": "dummy"})()
            
            # Part H - Autonomy Safety Rules
            from agent.planning.verifier import verify_plan
            from agent.decision.critic import critique_plan, critic_score
            
            risk = getattr(node, "risk", 0.5)
            # Estimate confidence
            fb = verify_plan(plan)
            c_score = critic_score(plan, critique_plan(plan, {}))
            confidence = getattr(plan, "confidence", max(0.0, c_score * (1.0 - risk)))
            
            if risk > 0.7 or confidence < 0.6:
                print(f"[GoalEngine] Node requires approval! Risk: {risk:.2f}, Confidence: {confidence:.2f}")
                self.escalate_to_user("High risk / low confidence task requires approval.")
                self.is_interrupted = True # Pause until approved
                return

            execute_routed_node(node)
            self.update_goal_state(goal, {"success": True}, getattr(node, "id", "unknown_node"))
            
            # Phase 26: RL-lite Policy Update
            try:
                from agent.rl.policy_store import policy_store
                policy_store.update_policy(plan, {"success": True}, latency=0.1, rollbacks=0, repairs=0)
            except Exception:
                pass
                
            # Part F & H - Improvement Trigger & Loop
            try:
                from agent.self_evolve.reflection import process_execution
                process_execution(goal.objective, plan, {"success": True})
                
                from agent.learning.strategy_store import process_strategy_learning
                process_strategy_learning(goal.objective, plan, {"success": True})
                
                from agent.self_evolve.task_generator import curriculum_manager
                if goal.is_sandbox:
                    curriculum_manager.evaluate_training_result({"success": True})
            except Exception as e:
                print(f"[SelfEvolve] Failed to process execution: {e}")
            
            # Mark done if all done
            goal.status = "DONE"
            self.save_state()
            
        except Exception as e:
            print(f"[GoalEngine] Execution failed for {goal.objective}: {str(e)}")
            self.update_goal_state(goal, {"success": False, "error": str(e)}, "expansion_step")
            
            # Phase 26: RL-lite Policy Update
            try:
                from agent.rl.policy_store import policy_store
                if 'plan' in locals():
                    policy_store.update_policy(plan, {"success": False}, latency=0.1, rollbacks=0, repairs=0)
            except Exception:
                pass

            # Part F & H - Improvement Trigger & Loop
            try:
                if 'plan' in locals():
                    from agent.self_evolve.reflection import process_execution
                    process_execution(goal.objective, plan, {"success": False, "error": str(e)})
                    
                    from agent.learning.strategy_store import process_strategy_learning
                    process_strategy_learning(goal.objective, plan, {"success": False, "error": str(e)})
                    
                from agent.self_evolve.task_generator import curriculum_manager
                if goal.is_sandbox:
                    curriculum_manager.evaluate_training_result({"success": False, "error": str(e)})
                else:
                    # Part A - Skill Gap Detection
                    gap = curriculum_manager.detect_skill_gap({"success": False, "error": str(e)})
                    if gap:
                        print(f"[Curriculum] Detected skill gap: {gap}")
                        self._last_skill_gap = gap
            except Exception as ev_err:
                print(f"[SelfEvolve] Failed to process failure execution: {ev_err}")

goal_engine = GoalEngine()
