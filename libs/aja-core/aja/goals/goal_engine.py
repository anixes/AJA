import json
import os
import re
import time
import uuid
from typing import Any, Dict, List

import aja.config
from aja.config import DATA_DIR
from aja.planning.planner import Planner
from aja.planning.verifier import verify_plan
from aja.decision.critic import critic_score, critique_plan
from aja.runtime.event_bus import EVENTS, bus

GLOBAL_STATE_FILE = aja.config.DATA_DIR / "agent_state.json"




class Goal:
    def __init__(
        self,
        objective: str,
        priority: int,
        deadline: float = None,
        is_sandbox: bool = False,
    ):
        self.id = str(uuid.uuid4())[:8]
        self.objective = objective
        self.priority = priority
        self.deadline = deadline or float("inf")
        self.is_sandbox = is_sandbox
        self.subgoals = []
        self.status = "PENDING"  # PENDING -> PLANNING -> EXECUTING -> VERIFYING -> DONE / FAILED
        self.metadata = {}

        self.current_node_index = 0
        self.plan = None
        self.replan_count = 0
        self.max_replans = 2

        self.progress = {
            "completed_steps": [],
            "failed_steps": [],
            "current_state": "Not started",
        }
        self.failures = 0
        self.retries = 0

    def to_dict(self):
        plan_dict = None
        if self.plan and hasattr(self.plan, "to_dict"):
            try:
                plan_dict = self.plan.to_dict()
            except Exception as e:
                print(f"[GoalEngine] Failed to serialize plan: {e}")

        return {
            "id": self.id,
            "objective": self.objective,
            "priority": self.priority,
            "deadline": self.deadline,
            "is_sandbox": self.is_sandbox,
            "status": self.status,
            "progress": self.progress,
            "failures": self.failures,
            "retries": self.retries,
            "current_node_index": self.current_node_index,
            "replan_count": self.replan_count,
            "metadata": getattr(self, "metadata", {}),
            "plan": plan_dict,
        }

    @classmethod
    def from_dict(cls, data):
        g = cls(
            data["objective"],
            data["priority"],
            data.get("deadline"),
            data.get("is_sandbox", False),
        )
        g.id = data["id"]
        g.status = data.get("status", "PENDING")
        g.progress = data.get(
            "progress", {"completed_steps": [], "failed_steps": [], "current_state": ""}
        )
        g.failures = data.get("failures", 0)
        g.retries = data.get("retries", 0)
        g.current_node_index = data.get("current_node_index", 0)
        g.replan_count = data.get("replan_count", 0)
        g.metadata = data.get("metadata", {})

        plan_data = data.get("plan")
        if plan_data and isinstance(plan_data, dict):
            try:
                from aja.planning.models import PlanGraph
                g.plan = PlanGraph.from_dict(plan_data)
            except Exception as e:
                print(f"[GoalEngine] Failed to restore plan from dict: {e}")
                g.plan = None
        return g




class GoalEngine:
    def __init__(self):
        self.goals: List[Goal] = []
        self.planner = Planner()
        try:
            from aja.self_evolve.reflection import knowledge_base

            self.planner.bias(knowledge_base)
        except Exception as e:
            print(f"[GoalEngine] Failed to load knowledge base into planner: {e}")

        self.autonomy_enabled = True
        self.is_interrupted = False
        self.paused_mission_ids = set()  # Track granular interruptions
        self.max_retries = 3
        from aja.memory.secretary import get_aja_memory

        self.memory = get_aja_memory()
        self._last_poll_time = 0
        self.load_state()

    def add_goal(
        self,
        objective: str,
        priority: int = 1,
        deadline: float = None,
        is_sandbox: bool = False,
    ) -> str:
        # Save to LanceDB
        mission = self.memory.create_mission(objective, priority)
        # Also keep in memory for current loop
        g = Goal(objective, priority, deadline, is_sandbox)
        g.id = mission["mission_id"]
        self.goals.append(g)
        return g.id

    def get_active_goals(self) -> List[Goal]:
        active = [g for g in self.goals if g.status not in ["DONE", "FAILED", "PAUSED"]]
        return self.prioritize(active)

    def prioritize(self, goals: List[Goal]) -> List[Goal]:
        # urgent + high priority -> first
        # Sort by priority descending, then deadline ascending
        return sorted(goals, key=lambda g: (-g.priority, g.deadline))

    def _is_safe_read_only(self, objective: str) -> bool:
        obj = objective.strip().lower()
        if obj.startswith("/run "):
            obj = obj[5:].strip()
        elif obj.startswith("/"):
            obj = obj[1:].strip()

        obj = re.sub(r'^[\'"\\(]+|[\'"\\)]+$', "", obj).strip()

        for char in [";", "&&", "||", "|", "`", "$", ">", "<"]:
            if char in obj:
                return False
        safe_prefixes = ("dir", "ls", "type", "cat", "echo", "pwd", "whoami")
        words = obj.split()
        if not words:
            return False
        return words[0] in safe_prefixes

    def expand_goal(self, goal: Goal):
        from aja.planning.scorer import COMPLEXITY_LOW, estimate_complexity

        force_swarm = getattr(goal, "metadata", {}).get("force_swarm", False)
        if not force_swarm and estimate_complexity(goal.objective) == COMPLEXITY_LOW:
            print(
                f"[GoalEngine] Goal complexity is LOW: {goal.objective}. Bypassing LLM decomposition."
            )
            from aja.planning.planner import _fallback_graph

            return _fallback_graph(goal.objective)

        try:
            from aja.learning.strategy_store import strategy_store

            similar_strategies = strategy_store.search(goal.objective)
            trusted = [
                s
                for s in similar_strategies
                if strategy_store.score_experience(s) >= 0.7 and s["executions"] > 2
            ]
            experimental = [s for s in similar_strategies if s not in trusted]
            self.planner.bias_with_strategies(
                trusted, experimental, is_sandbox=goal.is_sandbox, risk_level=0.1
            )
        except Exception as e:
            print(f"[GoalEngine] Strategy Search error: {e}")

        # Build initial state context
        state = {
            "completed_steps": goal.progress.get("completed_steps", []),
            "current_objective": goal.objective,
            "system_operational": True,  # Assume true for start of autonomous loop
        }
        return self.planner.decompose(goal.objective, current_state=state)

    def update_goal_state(self, goal: Goal, result: Any, node_id: str):
        success = (
            getattr(result, "success", False)
            if not isinstance(result, dict)
            else result.get("success", False)
        )
        if success:
            goal.progress["completed_steps"].append(node_id)
            goal.progress["current_state"] = f"Completed {node_id}"
            goal.retries = 0
        else:
            goal.progress["failed_steps"].append(node_id)
            goal.failures += 1
            goal.retries += 1
            error = (
                getattr(result, "error", "")
                if not isinstance(result, dict)
                else result.get("error", "")
            )
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

    def escalate_to_user(self, message: str, mission_id: str = "system"):
        print(f"[ESCALATION] {message}")
        # Update mission status to indicate we are waiting
        if mission_id != "system":
            self.memory.update_mission(mission_id, {"status": "AWAITING_APPROVAL"})

        bus.publish(
            EVENTS["AWAITING_APPROVAL"], {"message": message, "mission_id": mission_id}
        )

    def disable_autonomy(self):
        print("[GoalEngine] System instability detected! Disabling autonomy.")
        self.autonomy_enabled = False

    def fallback_to_manual(self):
        print(
            "[GoalEngine] Falling back to manual mode. User approval required for all actions."
        )

    def modify_goal_strategy(self, goal: Goal):
        print(
            f"[GoalEngine] Modifying strategy for goal {goal.objective} due to failures."
        )
        try:
            from aja.self_build.capability_builder import self_build_cycle
            self_build_cycle(goal.objective)
        except Exception as e:
            print(f"[GoalEngine] Self-build strategy update skipped: {e}")

        goal.objective = f"fallback: {goal.objective}"
        goal.retries = 0


    def save_state(self):
        # Update LanceDB for each goal
        for g in self.goals:
            updates = {
                "status": g.status,
                "priority": g.priority,
                "result_summary": g.progress.get("current_state", ""),
                "metadata_json": json.dumps(g.to_dict()),
            }
            self.memory.update_mission(g.id, updates)

    def load_state(self):
        # Load from LanceDB instead of JSON
        missions = self.memory.list_missions()
        self.goals = []
        for m in missions:
            # Try to restore from metadata_json if exists, else raw mission data
            try:
                meta_str = m.get("metadata_json", "{}")
                data = json.loads(meta_str)
                # If metadata_json is just a custom dict or empty
                if not data or not isinstance(data, dict) or "objective" not in data:
                    data = {
                        "id": m["mission_id"],
                        "objective": m["goal"],
                        "priority": m["priority"],
                        "status": m["status"],
                    }
                    g = Goal.from_dict(data)
                    # Try to parse the custom dict as metadata directly
                    try:
                        parsed = json.loads(meta_str)
                        if isinstance(parsed, dict):
                            g.metadata = parsed
                    except Exception:
                        g.metadata = {}
                else:
                    g = Goal.from_dict(data)

                self.goals.append(g)
            except Exception as e:
                print(f"[GoalEngine] Error loading state for mission: {e}")

    def sync_external_missions(self):
        """Polls LanceDB for missions added by AJA/Gateway"""
        # 1. Pick up new missions
        pending = self.memory.list_missions(status="PENDING")
        for m in pending:
            if not any(g.id == m["mission_id"] for g in self.goals):
                print(f"[GoalEngine] Picked up new external mission: {m['goal']}")
                g = Goal(m["goal"], m["priority"])
                g.id = m["mission_id"]

                # Parse metadata if present
                meta_json = m.get("metadata_json")
                g.metadata = {}
                if meta_json:
                    try:
                        parsed = json.loads(meta_json)
                        if isinstance(parsed, dict):
                            if "metadata" in parsed and isinstance(
                                parsed["metadata"], dict
                            ):
                                g.metadata = parsed["metadata"]
                            else:
                                g.metadata = parsed
                    except Exception as e:
                        print(
                            f"[GoalEngine] Failed to parse metadata_json for new mission: {e}"
                        )

                self.goals.append(g)
                self.memory.update_mission(
                    m["mission_id"],
                    {"status": "ACTIVE", "assigned_worker": "local-terminal"},
                )

        # 2. Check if paused missions can be cleared
        if self.paused_mission_ids:
            active_missions = self.memory.list_missions(status="ACTIVE")
            active_ids = {m["mission_id"] for m in active_missions}

            # If a mission that was paused is now ACTIVE again, resume it
            cleared = self.paused_mission_ids.intersection(active_ids)
            for cid in cleared:
                print(f"[GoalEngine] Mission {cid} approved/resumed. Clearing pause.")
                self.paused_mission_ids.remove(cid)

    async def run_step(self):
        if not self.autonomy_enabled:
            return

        # Poll for new missions from AJA every 2 seconds
        if time.time() - self._last_poll_time > 2:
            self.sync_external_missions()
            self._last_poll_time = time.time()

        active = self.get_active_goals()
        if not active:
            return

        goal = active[0]

        # Part G - Granular Lock Check
        if goal.id in self.paused_mission_ids:
            found_next = False
            for g in active[1:]:
                if g.id not in self.paused_mission_ids:
                    goal = g
                    found_next = True
                    break
            if not found_next:
                return

        # FSM STATE DISPATCH

        if goal.status == "PENDING":
            await self._step_planning(goal)
        elif goal.status == "PLANNING":
            goal.status = "EXECUTING"
            self.save_state()
        elif goal.status == "EXECUTING":
            await self._step_executing(goal)
        elif goal.status == "VERIFYING":
            await self._step_verifying(goal)

    async def _step_planning(self, goal: Goal):
        print(f"\n[GoalEngine] Planning for goal: {goal.objective}")
        try:
            goal.plan = self.expand_goal(goal)
            goal.current_node_index = 0
            plan = goal.plan

            plan_summary = f"Objective: {goal.objective}"
            if hasattr(plan, "nodes") and plan.nodes:
                plan_summary += f"\nSteps: {len(plan.nodes)}"
                for i, n in enumerate(plan.nodes[:3]):
                    plan_summary += f"\n  {i + 1}. {getattr(n, 'task', 'step')}"
                if len(plan.nodes) > 3:
                    plan_summary += f"\n  ...and {len(plan.nodes) - 3} more"

            bus.publish(EVENTS["PLAN_CREATED"], {"plan_summary": plan_summary})
            import asyncio as _asyncio
            await _asyncio.to_thread(
                self.memory.record_scheduler_event,
                kind="PLAN_CREATED",
                target=goal.id,
                metadata={"plan_summary": plan_summary, "message": plan_summary},
                status=True,
            )
            goal.status = "PLANNING"
            self.save_state()
        except Exception as e:
            print(f"[GoalEngine] Planning failed for {goal.objective}: {str(e)}")
            goal.status = "FAILED"
            self.save_state()

    async def _step_executing(self, goal: Goal):
        plan = goal.plan
        nodes = []
        if hasattr(plan, "nodes"):
            nodes = plan.nodes
        elif isinstance(plan, list) and plan:
            nodes = plan

        if not nodes:
            node = type("Node", (), {"id": "n1", "risk": 0.5, "task": goal.objective, "dependencies": []})()
            nodes = [node]

        completed = set(goal.progress.get("completed_steps", []))
        if len(completed) >= len(nodes):
            goal.status = "VERIFYING"
            self.save_state()
            return

        # Find all ready nodes whose dependencies are satisfied and haven't completed
        ready_nodes = []
        for n in nodes:
            nid = getattr(n, "id", f"node_{len(ready_nodes)}")
            if nid in completed:
                continue
            deps = getattr(n, "dependencies", [])
            if not deps or all(d in completed for d in deps):
                ready_nodes.append(n)

        if not ready_nodes:
            uncompleted = [n for n in nodes if getattr(n, "id", "") not in completed]
            if not uncompleted:
                goal.status = "VERIFYING"
                self.save_state()
            else:
                print(f"[GoalEngine] Waiting on unresolved dependencies for nodes: {[getattr(n, 'id', '') for n in uncompleted]}")
            return

        import anyio
        from aja.orchestration.swarm import SwarmEngine

        async def _run_single_node(node):
            node_id = getattr(node, "id", "node_0")
            task_str = getattr(node, "task", goal.objective)

            # Safety & Risk check
            risk = getattr(node, "risk", 0.5)
            state = {
                "completed_steps": goal.progress.get("completed_steps", []),
                "system_operational": True,
            }
            fb = verify_plan(plan, state=state) if (plan and hasattr(plan, "goal")) else None
            c_score = critic_score(plan, critique_plan(plan, {})) if (plan and hasattr(plan, "nodes")) else 1.0
            confidence = getattr(plan, "confidence", max(0.0, c_score * (1.0 - risk * 0.5)))

            if c_score == 0 and risk == 0.5 and self._is_safe_read_only(goal.objective):
                confidence = 0.75

            if risk > 0.7 or confidence < 0.6:
                print(f"[GoalEngine] Node {node_id} requires approval! Risk: {risk:.2f}, Confidence: {confidence:.2f}")
                self.escalate_to_user(
                    "High risk / low confidence task requires approval.",
                    mission_id=goal.id,
                )
                self.paused_mission_ids.add(goal.id)
                return

            print(f"\n[GoalEngine] Executing Node [{node_id}]: {task_str}")

            try:
                engine = SwarmEngine()
                result = await engine.execute_direct(task_str)

                if "node_outputs" not in goal.progress:
                    goal.progress["node_outputs"] = {}
                goal.progress["node_outputs"][node_id] = {"success": True, "result": str(result) if result else "OK"}

                self.update_goal_state(goal, {"success": True}, node_id)
                goal.current_node_index = len(goal.progress.get("completed_steps", []))
                goal.retries = 0
                self.save_state()

            except Exception as e:
                print(f"[GoalEngine] Execution failed for node {node_id}: {str(e)}")
                self.update_goal_state(goal, {"success": False, "error": str(e)}, node_id)

                if goal.retries <= self.max_retries:
                    print(f"[GoalEngine] Node {node_id} retry {goal.retries}/{self.max_retries} scheduled.")
                elif goal.replan_count < goal.max_replans:
                    print(f"[GoalEngine] Node {node_id} retries exhausted. Triggering re-plan ({goal.replan_count + 1}/{goal.max_replans}).")
                    goal.replan_count += 1
                    goal.retries = 0
                    goal.status = "PENDING"
                    self.save_state()
                else:
                    print(f"[GoalEngine] Goal {goal.objective} max re-plans reached. Marking FAILED.")
                    goal.status = "FAILED"
                    self.save_state()
                    self.escalate_to_user(f"Goal {goal.objective} repeatedly failed after max re-plans.", mission_id=goal.id)

        async with anyio.create_task_group() as tg:
            for node in ready_nodes:
                tg.start_soon(_run_single_node, node)


    async def _step_verifying(self, goal: Goal):
        plan = goal.plan
        try:
            from aja.rl.policy_store import policy_store
            if plan:
                policy_store.update_policy(
                    plan, {"success": True}, latency=0.1, rollbacks=0, repairs=0
                )
        except Exception:
            pass

        try:
            if plan:
                from aja.self_evolve.reflection import process_execution
                process_execution(goal.objective, plan, {"success": True})

                from aja.learning.strategy_store import process_strategy_learning
                process_strategy_learning(goal.objective, plan, {"success": True})

            from aja.self_evolve.task_generator import curriculum_manager
            if goal.is_sandbox:
                curriculum_manager.evaluate_training_result({"success": True})
        except Exception as e:
            print(f"[SelfEvolve] Failed to process execution: {e}")

        goal.status = "DONE"
        self.save_state()
        import asyncio as _asyncio
        await _asyncio.to_thread(
            self.memory.record_scheduler_event,
            kind="MISSION_DONE",
            target=goal.id,
            metadata={"message": f"Goal completed successfully: {goal.objective}"},
            status=True,
        )



goal_engine = GoalEngine()
