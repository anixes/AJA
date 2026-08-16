"""
AJA Cognitive Engine: Cognitive Orchestrator
Coordinates the CoALA Perception-Retrieval-Reasoning-Action Loop,
Magentic-One Specialist Delegation, CodeAct Execution, and Post-Task Reflection.
"""

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from aja.cognitive.codeact import CodeActExecutor, CodeActResult
from aja.cognitive.memory_manager import CognitiveMemoryManager
from aja.cognitive.memory_models import (
    EpisodeReflection,
    ProceduralSkill,
    TaskTrajectory,
    TrajectoryStep,
    WorkingMemory,
)
from aja.cognitive.prompts import build_system_prompt
from aja.cognitive.skill_compiler import SkillCompiler
from aja.cognitive.specialists import (
    BaseSpecialist,
    CodeEngineerSpecialist,
    SysAdminSpecialist,
    WebResearchSpecialist,
)
from aja.cognitive.ttc_planner import TTCPlanner
from aja.mcp.mcp_client_manager import MCPClientManager
from aja.orchestration.tools.sys_tools import (
    get_active_ports,
    get_disk_usage,
    get_service_status,
    get_system_specs,
    inspect_docker_containers,
)
from aja.orchestration.tools.web_tools import fetch_url, search_web

logger = logging.getLogger(__name__)


class CognitiveOrchestrator:
    """
    Lead Cognitive Orchestrator (August 2026 Frontier Architecture).
    Synthesizes Bi-Temporal Knowledge Graph Memory (CoALA 2.0 / Letta),
    System-2 Test-Time Compute (TTC) Dynamic Tree Search,
    Autonomous Skill Self-Evolution (agentskills.io),
    and Stateless Universal MCP 2026 Tool Mesh.
    """

    def __init__(
        self,
        memory_manager: Optional[CognitiveMemoryManager] = None,
        codeact_executor: Optional[CodeActExecutor] = None,
        ttc_planner: Optional[TTCPlanner] = None,
        skill_compiler: Optional[SkillCompiler] = None,
        mcp_mesh: Optional[MCPClientManager] = None,
    ):
        self.memory = memory_manager or CognitiveMemoryManager()
        self.codeact = codeact_executor or CodeActExecutor()
        self.ttc_planner = ttc_planner or TTCPlanner()
        self.skill_compiler = skill_compiler or SkillCompiler(skills_dir=self.memory.skills_dir)
        self.mcp_mesh = mcp_mesh or MCPClientManager()

        self.specialists: Dict[str, BaseSpecialist] = {
            "sysadmin": SysAdminSpecialist(),
            "web_research": WebResearchSpecialist(),
            "coding": CodeEngineerSpecialist(),
        }

    def route_specialist(self, goal: str) -> BaseSpecialist:
        """Heuristic Magentic-One specialist router based on goal intent."""
        goal_lower = goal.lower()
        if any(kw in goal_lower for kw in ["search", "web", "find out", "research", "lookup", "docs", "documentation", "fetch", "url", "changelog"]):
            return self.specialists["web_research"]
        elif any(kw in goal_lower for kw in ["refactor", "pytest", "unit test", "fix bug", "function", "class", "git commit", "code", "compile"]):
            return self.specialists["coding"]
        return self.specialists["sysadmin"]  # Default ambient specialist

    def build_contextual_prompt(self, goal: str, specialist: BaseSpecialist) -> str:
        """Assembles prompt with CoALA Semantic Environment Facts + Past Episodic Lessons."""
        semantic_summary = self.memory.get_semantic_context_summary()
        past_episodes = self.memory.recall_episodes(goal, limit=2)
        skills = self.memory.list_skills()

        return build_system_prompt(
            goal=goal,
            specialist_name=specialist.name,
            specialist_instructions=specialist.get_system_instructions(),
            semantic_summary=semantic_summary,
            past_episodes=past_episodes,
            available_skills=skills,
        )

    async def execute_mission(
        self,
        goal: str,
        domain: Optional[str] = None,
        cwd: Optional[Path] = None,
        max_turns: int = 5,
        use_ttc: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes a full mission across the CoALA loop with live action execution,
        trajectory tracking, System-2 TTC tree search, and post-task self-evolution.
        """
        if use_ttc:
            async def _ttc_step_exec(step: Dict[str, Any]) -> Any:
                payload = step.get("payload", "")
                res = self.codeact.execute(payload, cwd=cwd or Path.home())
                if res.exit_code != 0:
                    raise RuntimeError(f"Step failed with exit code {res.exit_code}: {res.stderr}")
                return res.output

            ttc_result = await self.ttc_planner.execute_with_tree_search(
                goal=goal,
                executor_fn=_ttc_step_exec,
                context_summary=self.memory.get_semantic_context_summary(),
            )
            return {
                "task_id": str(uuid.uuid4()),
                "goal": goal,
                "specialist": "system2_ttc_planner",
                "success": ttc_result.get("success", False),
                "output": "\n".join(str(o) for o in ttc_result.get("outputs", [])) or ttc_result.get("error", ""),
                "ttc_details": ttc_result,
            }

        task_id = str(uuid.uuid4())
        working_memory = self.memory.create_working_memory(task_id=task_id, goal=goal)

        # 1. Select Specialist & Domain
        specialist = self.specialists.get(domain or "") or self.route_specialist(goal)
        trajectory = TaskTrajectory(
            episode_id=task_id,
            goal=goal,
            domain=specialist.name,
        )

        exec_cwd = (cwd or Path.home()).resolve()
        start_time = time.perf_counter()
        step_index = 1
        final_output = ""
        success = True
        critique = "Mission executed successfully."
        lessons = []
        auto_skill_name = None

        try:
            # 2. Native Tool Execution Shortcut for Common Ambient Queries
            goal_lower = goal.lower()
            if "system specs" in goal_lower or "host info" in goal_lower or ("specs" in goal_lower and "system" in goal_lower):
                specs = get_system_specs()
                final_output = f"Host System Specifications:\n" + "\n".join(f"- {k}: {v}" for k, v in specs.items())
                trajectory.steps.append(TrajectoryStep(
                    step_index=step_index,
                    action_type="tool_call",
                    action_payload="get_system_specs()",
                    observation=specs,
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                ))

            elif "disk" in goal_lower and ("usage" in goal_lower or "space" in goal_lower):
                disk = get_disk_usage(str(exec_cwd))
                final_output = f"Disk Usage for {disk.get('path', 'root')}:\n" + "\n".join(f"- {k}: {v}" for k, v in disk.items())
                trajectory.steps.append(TrajectoryStep(
                    step_index=step_index,
                    action_type="tool_call",
                    action_payload="get_disk_usage()",
                    observation=disk,
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                ))

            elif "docker" in goal_lower and ("container" in goal_lower or "ps" in goal_lower or "status" in goal_lower):
                containers = inspect_docker_containers()
                final_output = f"Docker Containers ({len(containers)} detected):\n" + json_format(containers)
                trajectory.steps.append(TrajectoryStep(
                    step_index=step_index,
                    action_type="tool_call",
                    action_payload="inspect_docker_containers()",
                    observation=containers,
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                ))

            elif "search" in goal_lower or "find out" in goal_lower:
                # Extract query
                query = re.sub(r"^(search( for)?|find out( about)?)\s*", "", goal, flags=re.IGNORECASE).strip()
                search_results = search_web(query or goal, limit=5)
                final_output = f"Web Search Results for '{query or goal}':\n\n"
                for res in search_results:
                    final_output += f"### {res['title']}\n- URL: {res['url']}\n- {res['snippet']}\n\n"

                trajectory.steps.append(TrajectoryStep(
                    step_index=step_index,
                    action_type="tool_call",
                    action_payload=f"search_web('{query}')",
                    observation=search_results,
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                ))

            else:
                # 3. CodeAct Execution Action (Fallback to direct shell / python execution)
                codeact_res = self.codeact.execute(goal, cwd=exec_cwd)
                final_output = codeact_res.output
                success = (codeact_res.exit_code == 0)
                if not success:
                    critique = f"CodeAct exited with code {codeact_res.exit_code}: {codeact_res.stderr[:200]}"
                    lessons.append("Ensure dependencies and shell path syntax are verified.")

                trajectory.steps.append(TrajectoryStep(
                    step_index=step_index,
                    action_type=f"codeact_{codeact_res.language}",
                    action_payload=goal,
                    observation=codeact_res.output,
                    duration_ms=codeact_res.duration_ms,
                    status=codeact_res.status,
                ))

        except Exception as e:
            success = False
            final_output = f"Mission execution encountered error: {e}"
            critique = f"Unhandled exception: {str(e)}"
            lessons.append("Check execution environment preconditions.")
            logger.error("Mission failed: %s", e)

        finally:
            # 4. Post-Task Reflection & Episodic Memory Persistence
            trajectory.mark_completed(success=success, critique=critique, lessons=lessons)
            self.memory.save_episode(trajectory)
            self.memory.clear_working_memory(task_id)

            # 5. Self-Evolution: Autonomous Skill Compilation for multi-step winning missions
            if success and len(trajectory.steps) >= 2:
                try:
                    compiled = self.skill_compiler.distill_trajectory(trajectory)
                    if compiled and compiled.is_valid and compiled.skill_obj:
                        self.memory.register_skill(compiled.skill_obj)
                        auto_skill_name = compiled.skill_name
                        logger.info("Self-Evolution: Auto-compiled skill '%s'", auto_skill_name)
                except Exception as ex:
                    logger.debug("Skill compilation skipped: %s", ex)

        total_duration_ms = (time.perf_counter() - start_time) * 1000.0
        return {
            "task_id": task_id,
            "goal": goal,
            "specialist": specialist.name,
            "success": success,
            "output": final_output,
            "duration_ms": total_duration_ms,
            "auto_compiled_skill": auto_skill_name,
            "reflection": {
                "success": success,
                "critique": critique,
                "lessons_learned": lessons,
            },
        }


def json_format(obj: Any) -> str:
    import json
    return json.dumps(obj, indent=2)
