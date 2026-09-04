"""
roles.py - Heterogeneous Role Dispatcher for AJA (OpenCode 2 Pattern).
======================================================================
Coordinates multi-model execution by decomposing missions into discrete roles:
- Planner: High-reasoning model (Cloud Gemini/Claude/OpenAI) for task breakdown.
- Worker: Fast/local model (llama.cpp with GBNF or fast cloud) for tool execution.
- Verifier: Validates acceptance criteria and workspace health before completion.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RoleConfig:
    """Configures the models used for heterogeneous mission roles."""

    planner_model: str = "google:gemini-2.5-flash"
    worker_model: str = "llama_cpp:qwen2.5-coder-7b-instruct-q3_k_m"
    verifier_model: Optional[str] = None

    def __post_init__(self):
        if self.verifier_model is None:
            self.verifier_model = self.planner_model


@dataclass
class MissionStep:
    """An atomic, verifiable unit of work within a multi-step mission."""

    step_id: int
    title: str
    description: str
    acceptance_criteria: List[str] = field(default_factory=list)
    verification_cmd: Optional[str] = None
    status: str = "pending"  # pending, in_progress, completed, failed
    result: Optional[Dict[str, Any]] = None


class HeterogeneousOrchestrator:
    """
    Orchestrates execution across specialized roles:
    Planner (Reasoning) -> Worker (Local GBNF Execution) -> Verifier (Certification).
    """

    def __init__(
        self,
        config: Optional[RoleConfig] = None,
        gateway=None,
        dry_run: bool = False,
    ):
        self.config = config or RoleConfig()
        self.dry_run = dry_run
        self._gateway = gateway

    @property
    def gateway(self):
        if self._gateway is None:
            from aja.orchestration.gateway import LLMGateway

            self._gateway = LLMGateway()
        return self._gateway

    async def plan(self, objective: str) -> List[MissionStep]:
        """Decompose a high-level goal into an ordered sequence of MissionSteps."""
        planner_prompt = f"""You are an elite Software Architecture Planner.
Analyze this objective and break it down into an ordered, minimal list of concrete executable steps.

Objective:
{objective}

Output ONLY a valid JSON array of step objects with this exact structure:
[
  {{
    "step_id": 1,
    "title": "Short title",
    "description": "Specific instruction of what code to edit, run, or create",
    "acceptance_criteria": ["Criteria 1", "Criteria 2"],
    "verification_cmd": "optional shell command like pytest or python -m py_compile"
  }}
]
"""
        response = await self.gateway.chat(
            model=self.config.planner_model,
            prompt=[{"role": "user", "content": planner_prompt}],
            system="You are a strict JSON-only mission decomposition bot.",
        )

        resp_text = response if isinstance(response, str) else response.get("content", "")
        start_idx = resp_text.find("[")
        end_idx = resp_text.rfind("]")

        steps: List[MissionStep] = []
        if start_idx != -1 and end_idx != -1:
            try:
                raw_steps = json.loads(resp_text[start_idx : end_idx + 1])
                for item in raw_steps:
                    steps.append(
                        MissionStep(
                            step_id=int(item.get("step_id", len(steps) + 1)),
                            title=str(item.get("title", f"Step {len(steps) + 1}")),
                            description=str(item.get("description", "")),
                            acceptance_criteria=list(item.get("acceptance_criteria", [])),
                            verification_cmd=item.get("verification_cmd"),
                        )
                    )
            except Exception as e:
                logger.warning("Failed to parse planner JSON: %s. Falling back to single step.", e)

        if not steps:
            steps.append(
                MissionStep(
                    step_id=1,
                    title="Direct Execution",
                    description=objective,
                    acceptance_criteria=["Objective successfully completed"],
                )
            )

        return steps

    async def execute_step(
        self,
        step: MissionStep,
        *,
        tools_registry,
        executor,
        session_history: Optional[List[Dict[str, str]]] = None,
        console=None,
    ) -> Dict[str, Any]:
        """Execute a single step using the configured worker model."""
        from aja.orchestration.direct_loop import run_direct_loop

        step.status = "in_progress"
        if console:
            console.print(
                f"\n[bold cyan]▶ [Worker: {self.config.worker_model}][/bold cyan] "
                f"Executing Step {step.step_id}: [italic]{step.title}[/italic]"
            )

        step_objective = (
            f"Step {step.step_id}: {step.title}\n"
            f"Details: {step.description}\n"
            f"Acceptance Criteria: {', '.join(step.acceptance_criteria)}"
        )

        outcome = await run_direct_loop(
            step_objective,
            gateway=self.gateway,
            tools_registry=tools_registry,
            executor=executor,
            model=self.config.worker_model,
            verification_cmd=step.verification_cmd,
            auto_verify=True,
            session_history=session_history,
            console=console,
            dry_run=self.dry_run,
        )

        step.result = outcome
        if outcome and outcome.get("status") == "completed":
            step.status = "completed"
        else:
            step.status = "failed"

        return outcome or {"status": "failed"}

    async def run_mission(
        self,
        objective: str,
        *,
        tools_registry,
        executor,
        console=None,
    ) -> Dict[str, Any]:
        """Execute a full multi-role mission: Plan -> Execute all steps -> Certify."""
        if console:
            console.print(f"[bold green]🎯 Initiating Heterogeneous Mission:[/] {objective}")
            console.print(
                f"[dim]Roles: Planner={self.config.planner_model} | Worker={self.config.worker_model}[/dim]"
            )

        # 1. Plan Stage
        steps = await self.plan(objective)
        if console:
            console.print(f"[bold cyan]📋 Plan generated with {len(steps)} step(s):[/]")
            for s in steps:
                console.print(f"  {s.step_id}. [bold]{s.title}[/]: {s.description}")

        # 2. Execution Stage
        session_history: List[Dict[str, str]] = []
        completed_steps = 0

        for step in steps:
            step_outcome = await self.execute_step(
                step,
                tools_registry=tools_registry,
                executor=executor,
                session_history=session_history,
                console=console,
            )
            if step.status == "completed":
                completed_steps += 1
            else:
                if console:
                    console.print(f"[bold red]✘ Mission halted on Step {step.step_id} failure.[/bold red]")
                return {
                    "status": "failed",
                    "failed_step": step.step_id,
                    "completed_steps": completed_steps,
                    "total_steps": len(steps),
                    "steps": [s.__dict__ for s in steps],
                }

        if console:
            console.print(f"\n[bold green]✔ All {len(steps)} step(s) completed and verified![/bold green]")

        return {
            "status": "completed",
            "completed_steps": completed_steps,
            "total_steps": len(steps),
            "steps": [s.__dict__ for s in steps],
        }
