"""
goal_session.py — Relentless goal execution for AJA.
====================================================
Provides GoalSession (Direct Worker loop) and GoalSwarmSession (Planner+Workers+Critic loop).
Both commands are relentless and run until `<signal>GOAL_COMPLETE</signal>` or `<signal>GOAL_FAILED: reason</signal>` is emitted.
"""

import asyncio
import re
from typing import Tuple

from aja.interface.modern import console
from aja.orchestration.direct_session import DirectSession
from aja.orchestration.swarm import SwarmEngine
from aja.config import AJA_PLANNER_MODEL, AJA_WORKER_MODEL


def _parse_signal(reply: str) -> Tuple[str, str]:
    """
    Returns ("complete", "") | ("failed", reason) | ("continue", "")
    Only matches signals outside of markdown code fences to prevent false-positives.
    """
    if not reply:
        return ("continue", "")

    # Strip all content inside ``` or ` blocks first
    cleaned = re.sub(r"```.*?```", "", reply, flags=re.DOTALL)
    cleaned = re.sub(r"`[^`]+`", "", cleaned)

    if "<signal>GOAL_COMPLETE</signal>" in cleaned:
        return ("complete", "")
    
    m = re.search(r"<signal>GOAL_FAILED:(.*?)</signal>", cleaned, re.DOTALL)
    if m:
        return ("failed", m.group(1).strip())
    
    return ("continue", "")


class GoalSession:
    """
    Relentless single-agent execution loop for /goal.
    Uses DirectSession under the hood for fast, prompt-cached tool execution.
    """

    AUDIT_PROMPT = (
        "Audit your work on the original goal carefully. "
        "Use your tools to check files exist, run tests, or verify correctness. "
        "If 100% verifiably achieved, output exactly on its own line:\n"
        "<signal>GOAL_COMPLETE</signal>\n"
        "If genuinely impossible or blocked by an unresolvable dependency, output:\n"
        "<signal>GOAL_FAILED: reason</signal>\n"
        "If anything is incomplete or broken, fix it now without asking for permission."
    )

    def __init__(self, model=None, dry_run: bool = False, max_iterations: int = 10, timeout_seconds: int = 600):
        self.session = DirectSession(model=model, dry_run=dry_run)
        self.dry_run = dry_run
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds

    def _last_assistant_reply(self) -> str:
        for msg in reversed(self.session.session_history):
            if msg["role"] == "assistant":
                return str(msg.get("content", ""))
        return ""

    async def _summarize_progress(self, objective: str, iteration: int) -> None:
        """Replace bloated history with a compact progress summary before audit turns."""
        recent_turns = self.session.session_history[-6:]  # Keep last 3 user/assistant pairs

        summary_msg = {
            "role": "system",
            "content": (
                f"[GOAL MODE — PROGRESS SUMMARY]\n"
                f"Original goal: {objective}\n"
                f"Iterations completed: {iteration - 1}\n"
                f"Showing last {len(recent_turns)} turns for context. "
                f"Earlier turns have been summarized to preserve context budget."
            )
        }
        # Replace history: summary header + recent turns only
        self.session.session_history = [summary_msg] + recent_turns

    async def run(self, objective: str) -> None:
        async def _loop():
            for i in range(1, self.max_iterations + 1):
                if i > 1:
                    await self._summarize_progress(objective, i)
                    prompt = self.AUDIT_PROMPT
                else:
                    prompt = (
                        f"GOAL: {objective}\n\n"
                        f"IMPORTANT: When you have fully achieved this goal, you MUST output exactly:\n"
                        f"<signal>GOAL_COMPLETE</signal>\n"
                        f"If it is impossible, output:\n"
                        f"<signal>GOAL_FAILED: reason</signal>"
                    )
                
                await self.session._turn(prompt, console, interactive=False)
                last = self._last_assistant_reply()
                
                status, reason = _parse_signal(last)
                
                if status == "complete":
                    console.print(f"\n[bold green]✔ Goal complete after {i} iteration(s)![/bold green]")
                    return
                elif status == "failed":
                    console.print(f"\n[bold red]✘ Goal failed: {reason}[/bold red]")
                    console.print("[dim]The agent determined this goal cannot be completed. Review manually.[/dim]")
                    return
            
            console.print(f"\n[yellow]⚠ Max iterations ({self.max_iterations}) reached. Review manually.[/yellow]")

        try:
            await asyncio.wait_for(_loop(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            console.print(f"\n[red]⏱ Goal timed out after {self.timeout_seconds}s.[/red]")


class GoalSwarmSession:
    """
    Relentless multi-agent execution loop for /swarm.
    Uses Planner + Workers for execution, and a separate Critic model for auditing.
    """

    def __init__(self, dry_run: bool = False, max_iterations: int = 8, timeout_seconds: int = 600):
        self.planner_engine = SwarmEngine(model=AJA_PLANNER_MODEL, dry_run=dry_run)
        self.critic_engine  = SwarmEngine(model=AJA_WORKER_MODEL, dry_run=dry_run)
        self.dry_run = dry_run
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds

    async def run(self, objective: str) -> None:
        async def _loop():
            failure_context = ""
            for i in range(1, self.max_iterations + 1):
                console.print(f"\n[dim]══ Swarm Iteration {i}/{self.max_iterations} ══[/dim]")

                full_goal = objective
                if failure_context:
                    full_goal = (
                        f"{objective}\n\n[PREVIOUS ATTEMPT — CRITIC FEEDBACK]\n"
                        f"{failure_context}\nAdapt your plan to fix these issues."
                    )

                # Plan + Execute
                await self.planner_engine.plan_and_execute_batons(full_goal, run_id=f"swarm_goal_{i}")

                # Critic audits
                audit_result = await self._run_critic(objective)
                status, reason = _parse_signal(audit_result)
                
                if status == "complete":
                    console.print(f"\n[bold green]✔ Swarm goal complete after {i} iteration(s)![/bold green]")
                    return
                elif status == "failed":
                    console.print(f"\n[bold red]✘ Swarm goal failed: {reason}[/bold red]")
                    console.print("[dim]The Swarm Critic determined this goal cannot be completed. Review manually.[/dim]")
                    return

                failure_context = audit_result
                preview = failure_context[:200] + "..." if len(failure_context) > 200 else failure_context
                console.print(f"\n[yellow]⚠ Critic found issues: {preview}[/yellow]")

            console.print(f"\n[yellow]⚠ Max swarm iterations ({self.max_iterations}) reached. Review manually.[/yellow]")

        try:
            await asyncio.wait_for(_loop(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            console.print(f"\n[red]⏱ Swarm goal timed out after {self.timeout_seconds}s.[/red]")

    async def _run_critic(self, objective: str) -> str:
        """
        Run Critic model to audit current state against original objective.
        This uses execute_direct on the SwarmEngine, which ensures the Critic has 
        access to the NativeToolRegistry to run tests and read files.
        """
        prompt = (
            f"Audit whether this goal has been fully achieved:\nGOAL: {objective}\n\n"
            f"Use your available tools to read files, run tests/pytest, and inspect command output. Do not guess.\n"
            f"If 100% achieved with concrete evidence, output exactly on its own line:\n"
            f"<signal>GOAL_COMPLETE</signal>\n"
            f"If genuinely impossible or blocked by an unresolvable dependency, output:\n"
            f"<signal>GOAL_FAILED: reason</signal>\n"
            f"Otherwise, describe specifically what still needs fixing. The Planner will use your report to re-plan."
        )
        history = []
        await self.critic_engine.execute_direct(prompt, session_history=history, interactive=False)
        for msg in reversed(history):
            if msg["role"] == "assistant":
                return str(msg.get("content", ""))
        return ""
