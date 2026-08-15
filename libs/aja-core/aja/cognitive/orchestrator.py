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

from aja.cognitive.codeact import CodeActExecutor
from aja.cognitive.memory_manager import CognitiveMemoryManager
from aja.cognitive.memory_models import (
    EpisodeReflection,
    ProceduralSkill,
    TaskTrajectory,
    TrajectoryStep,
    WorkingMemory,
)
from aja.cognitive.specialists import (
    BaseSpecialist,
    CodeEngineerSpecialist,
    SysAdminSpecialist,
    WebResearchSpecialist,
)
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
    Lead Cognitive Orchestrator.
    Synthesizes Tripartite Memory (CoALA), Ambient CodeAct Actions,
    and Dynamic Specialist Routing (Magentic-One).
    """

    def __init__(
        self,
        memory_manager: Optional[CognitiveMemoryManager] = None,
        codeact_executor: Optional[CodeActExecutor] = None,
    ):
        self.memory = memory_manager or CognitiveMemoryManager()
        self.codeact = codeact_executor or CodeActExecutor()

        self.specialists: Dict[str, BaseSpecialist] = {
            "sysadmin": SysAdminSpecialist(),
            "web_research": WebResearchSpecialist(),
            "coding": CodeEngineerSpecialist(),
        }

    def route_specialist(self, goal: str) -> BaseSpecialist:
        """Determines the most appropriate specialist role for the given goal."""
        goal_lower = goal.lower()
        if re.search(r"\b(docker|system|disk|cpu|ram|memory|port|service|status|vps|server|nginx|logs|systemctl)\b", goal_lower):
            return self.specialists["sysadmin"]
        elif re.search(r"\b(code|refactor|test|pytest|git|commit|function|class|bug|patch|pr)\b", goal_lower):
            return self.specialists["coding"]
        elif re.search(r"\b(search|web|url|scrape|docs|documentation|article|find out|paper|research)\b", goal_lower):
            return self.specialists["web_research"]
        return self.specialists["sysadmin"]  # Default ambient specialist

    def build_contextual_prompt(self, goal: str, specialist: BaseSpecialist) -> str:
        """Assembles prompt with CoALA Semantic Environment Facts + Past Episodic Lessons."""
        semantic_summary = self.memory.get_semantic_context_summary()
        past_episodes = self.memory.recall_episodes(goal, limit=2)

        prompt_lines = [
            f"=== AJA COGNITIVE ORCHESTRATOR [{specialist.name.upper()}] ===",
            specialist.get_system_instructions(),
            "",
            semantic_summary,
            "",
        ]

        if past_episodes:
            prompt_lines.append("### Relevant Past Experiences & Lessons Learned:")
            for ep in past_episodes:
                goal_txt = ep.get("goal", "")
                refl = ep.get("reflection") or {}
                critique = refl.get("critique", "")
                lessons = ", ".join(refl.get("lessons_learned") or [])
                prompt_lines.append(f"- **Past Goal**: {goal_txt}")
                if critique:
                    prompt_lines.append(f"  * Critique: {critique}")
                if lessons:
                    prompt_lines.append(f"  * Lessons: {lessons}")
            prompt_lines.append("")

        prompt_lines.extend([
            "### Goal to Execute:",
            f"{goal}",
            "",
            "Formulate actions as Python or Shell CodeAct blocks (```python ... ``` or ```bash ... ```) or tool calls.",
        ])

        return "\n".join(prompt_lines)

    async def execute_mission(
        self,
        goal: str,
        domain: Optional[str] = None,
        cwd: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Executes a full mission across the CoALA loop with live action execution,
        trajectory tracking, and post-task self-critique.
        """
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
                # Formulate shell/python command
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

        total_duration_ms = (time.perf_counter() - start_time) * 1000.0
        return {
            "task_id": task_id,
            "goal": goal,
            "specialist": specialist.name,
            "success": success,
            "output": final_output,
            "duration_ms": total_duration_ms,
            "reflection": {
                "success": success,
                "critique": critique,
                "lessons_learned": lessons,
            },
        }


def json_format(obj: Any) -> str:
    import json
    return json.dumps(obj, indent=2)
