"""
direct_loop.py - Library-grade single-agent tool-calling loop.
==============================================================

Extracted from ``SwarmEngine.execute_direct`` so the core ReAct-style loop
(prompt gateway -> parse tool calls / bash blocks -> execute -> observe ->
repeat until synthesis) is importable and runnable WITHOUT AJA's OS
machinery: no LanceDB, no journals, no ``DATA_DIR`` creation, no batons.

Import-time purity contract
---------------------------
This module imports ONLY the standard library at module scope. Every AJA
dependency is either injected by the caller or resolved lazily inside
``run_direct_loop``:

* context-window compression / truncation -> ``history_compressor`` /
  ``result_truncator`` injectables (lazy default imports
  ``aja.orchestration.context_window``, which reads config but never writes).
* trace-id acquisition -> ``trace_id_fn`` injectable (lazy default imports
  ``aja.observability.telemetry``, stdlib-pure).
* structured synthesis -> lazy import of ``aja.llm_structured``
  (stdlib-pure, no external effects).

With pure-fake gateway/registry/executor plus trivial injectables, running
this loop never imports ``aja.config`` and therefore never creates
``AJA_DATA_DIR``. Presentation (rich console / presenter) is optional and
silent when omitted; observability is exposed via :class:`DirectLoopHooks`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["DirectLoopHooks", "run_direct_loop"]


@dataclass
class DirectLoopHooks:
    """Optional observability callbacks. All default to no-ops."""

    on_command: Optional[Callable[[str, Dict[str, Any]], Any]] = None
    """Called as ``on_command(command, result_dict)`` after each suggested shell command."""
    on_tool_result: Optional[Callable[[Any], Any]] = None
    """Called as ``on_tool_result(result_obj)`` for each dispatched native-tool result."""
    on_synthesis: Optional[Callable[[Any], Any]] = None
    """Called as ``on_synthesis(structured)`` when an ``output_contract`` synthesis succeeds."""


def _default_history_compressor(history, model=None, provider=None):
    # CONFIG-READ only (token budgets); never writes. Lazy so pure-harness
    # runs can inject their own and skip the import entirely.
    from aja.orchestration.context_window import compress_history

    compress_history(history, model=model, provider=provider)


def _default_result_truncator(raw_output: str) -> str:
    from aja.orchestration.context_window import (
        MAX_TOOL_RESULT_CHARS,
        truncate_tool_result,
    )

    return truncate_tool_result(raw_output, MAX_TOOL_RESULT_CHARS)


def _default_trace_id_fn() -> str:
    from aja.observability.telemetry import get_trace_id

    return get_trace_id()


def _extract_bash_commands(content: str) -> List[str]:
    commands: List[str] = []
    marker = "```bash" if "```bash" in content else ("```sh" if "```sh" in content else None)
    if marker:
        for part in content.split(marker)[1:]:
            cmd = part.split("```")[0].strip()
            if cmd:
                commands.append(cmd)
    return commands


async def run_direct_loop(
    objective: str,
    *,
    gateway,
    tools_registry,
    executor,
    system_prompt: Optional[str] = None,
    session_history: Optional[List[Dict[str, str]]] = None,
    output_contract: Optional[Dict[str, Any]] = None,
    max_turns: int = 25,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    dry_run: bool = False,
    interactive: bool = True,
    hooks: Optional[DirectLoopHooks] = None,
    presenter=None,
    console=None,
    history_compressor: Optional[Callable[..., None]] = None,
    result_truncator: Optional[Callable[[str], str]] = None,
    trace_id_fn: Optional[Callable[[], str]] = None,
    verification_cmd: Optional[str] = None,
    auto_verify: bool = False,
    max_verification_retries: int = 3,
    verification_fn: Optional[Callable[[], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Run the single-agent tool-calling loop to completion.

    Returns a status dict (never raises for ordinary termination):

    * ``{"status": "completed", "turns": N}``
    * ``{"status": "completed", "turns": N, "result": <structured>}`` when
      ``output_contract`` was supplied and synthesis succeeded.
    * ``{"status": "empty_response", "turns": N}`` when the model returned nothing.
    * ``{"status": "incomplete", "reason": "max_turns", "turns": N}`` when
      ``max_turns`` was exhausted while the model kept requesting actions.

    When ``session_history`` is provided it is mutated in place (caller-owned,
    enabling multi-turn sessions and provider-side prefix caching); otherwise
    a fresh ephemeral history is seeded from ``objective``.
    """
    hooks = hooks or DirectLoopHooks()
    compressor = history_compressor or _default_history_compressor
    truncator = result_truncator or _default_result_truncator
    trace_id_getter = trace_id_fn or _default_trace_id_fn

    if not system_prompt:
        try:
            from aja.cognitive.prompts import build_system_prompt

            system_prompt = build_system_prompt(goal=objective)
        except Exception:
            pass

    if session_history is not None:
        history = session_history
    else:
        history = [
            {"role": "user", "content": f"Please execute this task directly: {objective}"}
        ]

    iteration = 0
    verification_attempts = 0
    while iteration < max_turns:
        iteration += 1

        try:
            compressor(history, model=model, provider=provider)
        except Exception as e:  # best-effort: compression must never kill the loop
            logger.debug("History compression skipped: %s", e)

        try:
            response = await gateway.chat(
                model=model,
                prompt=history,
                system=system_prompt,
                tools=tools_registry.get_schemas(interactive=interactive),
            )
        except Exception as e:
            if console:
                console.print(f"[red][Direct Mode] LLM Chat Error: {e}[/red]")
            if dry_run:
                response = "I have simulated the direct task completion successfully, Sir."
            else:
                raise e

        if not response:
            if console:
                console.print("[yellow][Direct Mode] Empty response from assistant. Exiting.[/yellow]")
            return {"status": "empty_response", "turns": iteration}

        if isinstance(response, dict):
            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])
        else:
            content = response
            tool_calls = []

        if content:
            if presenter:
                presenter.assistant(content)
            history.append({"role": "assistant", "content": content})
        elif tool_calls:
            # Keep role alternation valid even with text-less tool-call turns.
            history.append({"role": "assistant", "content": f"[Invoking {len(tool_calls)} tool(s)]"})

        tools_executed = False
        if tool_calls:
            tools_executed = True
            formatted_calls = []
            for tc in tool_calls:
                t_name = tc.get("name")
                t_args_str = tc.get("arguments", "{}")
                try:
                    t_args = json.loads(t_args_str) if isinstance(t_args_str, str) else t_args_str
                except Exception:
                    t_args = {}
                formatted_calls.append({"tool": t_name, "args": t_args})

            if console:
                console.print(f"[bold cyan]⚙ Calling {len(formatted_calls)} Tool(s)...[/]")
            results = await executor.dispatch_tool_calls(
                tool_calls=formatted_calls,
                trace_id=trace_id_getter(),
                dry_run=dry_run,
            )
            for r in results:
                if hooks.on_tool_result:
                    hooks.on_tool_result(r)
                if console:
                    if r.success:
                        console.print(f"[bold green]✔ Tool '{r.tool}' succeeded[/bold green]")
                        if r.data:
                            from aja.utils.redact import redact_secrets
                            from rich.markup import escape

                            console.print(f"[dim]{escape(redact_secrets(str(r.data)))}[/dim]")
                    else:
                        from aja.utils.redact import redact_secrets
                        from rich.markup import escape

                        err_msg = r.error or getattr(r, "stderr", None) or r.data
                        console.print(
                            f"[bold red]✘ Tool '{r.tool}' failed: {escape(redact_secrets(str(err_msg)))}[/bold red]"
                        )

                raw_output = str(r.data or r.error or getattr(r, "stderr", "") or "")
                safe_output = truncator(raw_output)
                obs = f"Tool '{r.tool}' result:\n{safe_output}"
                history.append({"role": "user", "content": obs})

        commands = _extract_bash_commands(content)

        if not commands and not tools_executed:
            # Autonomous Verification Gate (OpenCode 2 style self-healing loop)
            needs_verification = bool(verification_cmd or auto_verify or verification_fn)
            if needs_verification and verification_attempts < max_verification_retries:
                verification_attempts += 1
                if console:
                    console.print(
                        f"[bold cyan]🔍 [Verification Gate] Running verification (attempt {verification_attempts}/{max_verification_retries})...[/]"
                    )

                passed = True
                failure_prompt = ""

                # 1. Custom verification function
                if verification_fn:
                    try:
                        import asyncio
                        res = await verification_fn() if asyncio.iscoroutinefunction(verification_fn) else verification_fn()
                        if isinstance(res, dict) and not res.get("passed", True):
                            passed = False
                            failure_prompt = res.get("message") or res.get("error") or str(res)
                        elif hasattr(res, "passed") and not res.passed:
                            passed = False
                            failure_prompt = getattr(res, "to_feedback_prompt", lambda: str(res))()
                    except Exception as e:
                        passed = False
                        failure_prompt = f"Verification function error: {e}"

                # 2. Command verifier
                if passed and verification_cmd:
                    from aja.orchestration.verification_runner import run_command_verifier

                    c_res = await run_command_verifier(verification_cmd)
                    if not c_res.passed:
                        passed = False
                        failure_prompt = c_res.to_feedback_prompt()

                # 3. Auto-verify syntax on Python files if enabled
                if passed and auto_verify:
                    from aja.orchestration.verification_runner import verify_python_syntax
                    from pathlib import Path

                    py_files = [
                        p for p in Path(".").glob("**/*.py")
                        if "venv" not in p.parts and ".git" not in p.parts
                    ][:50]
                    s_res = verify_python_syntax(py_files)
                    if not s_res.passed:
                        passed = False
                        failure_prompt = s_res.to_feedback_prompt()

                if not passed:
                    if console:
                        console.print(
                            f"[bold red]✘ [Verification Gate Failed][/bold red] Injecting failure feedback for self-correction..."
                        )
                    feedback_msg = (
                        f"{failure_prompt}\n\n"
                        f"[Autonomous Self-Healing: Attempt {verification_attempts} of {max_verification_retries}. "
                        "The task cannot be completed until verification passes. Please correct the code.]"
                    )
                    history.append({"role": "user", "content": feedback_msg})
                    continue  # Loop again to allow the model to fix the error!
                else:
                    if console:
                        console.print(f"[bold green]✔ [Verification Gate Passed][/bold green] All checks verified.")

            elif needs_verification and verification_attempts >= max_verification_retries:
                if console:
                    console.print(
                        f"[bold yellow]⚠ [Verification Gate] Maximum verification retries ({max_verification_retries}) exhausted.[/bold yellow]"
                    )
                return {"status": "incomplete", "reason": "verification_failed", "turns": iteration}

            # No further actions suggested: direct execution has finished.
            if console:
                console.print(f"\n[bold green][+] Direct In-Process task completed successfully.[/bold green]")
            if output_contract:
                from aja.llm_structured import structured_completion

                transcript = "\n".join(
                    f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history[-12:]
                )
                synthesis_prompt = (
                    f"Objective: {objective}\n\n"
                    f"Execution transcript:\n{transcript}\n\n"
                    "Synthesize the final result. Return ONLY JSON conforming to the schema."
                )
                structured = await structured_completion(
                    gateway,
                    synthesis_prompt,
                    output_contract,
                    system=system_prompt,
                    model=model,
                )
                if hooks.on_synthesis:
                    hooks.on_synthesis(structured)
                return {"status": "completed", "turns": iteration, "result": structured, "verified": needs_verification}
            return {"status": "completed", "turns": iteration, "verified": needs_verification}

        for cmd in commands:
            if presenter:
                presenter.command(cmd)

            if dry_run:
                from aja.security.command_guard import classify_command

                classification = classify_command(cmd)
                if console:
                    console.print(
                        f"[bold yellow][DRY-RUN AUDIT][/bold yellow] Command: '{cmd}' | "
                        f"Safety: {classification['decision'].upper()} (Risk: {classification['risk_level']})"
                    )
                sim_stdout = f"[DRY-RUN SIMULATION OUTPUT] Successfully simulated command: {cmd}"
                result = {"status": "success", "stdout": sim_stdout, "stderr": "", "code": 0}
            else:
                # This loop always runs on an event loop; the sync executor
                # would block it via thread.join(). Use the async-native path
                # when available, else offload the sync bridge to a thread.
                import asyncio

                execute_async = getattr(executor, "execute_async", None)
                if execute_async is not None:
                    result = await execute_async(cmd)
                else:
                    result = await asyncio.to_thread(executor.execute, cmd)

            if hooks.on_command:
                hooks.on_command(cmd, result)

            if console:
                if result.get("status") == "success":
                    console.print(f"[bold green]✔ Command succeeded with code {result.get('code', 0)}[/bold green]")
                    if result.get("stdout"):
                        from aja.utils.redact import redact_secrets

                        console.print(f"[dim]{redact_secrets(result['stdout'])}[/dim]")
                else:
                    console.print(
                        f"[bold red]✘ Command failed: {result.get('message', 'Unknown failure') or result.get('stderr')}[/bold red]"
                    )

            result_str = (
                f"Command executed: {cmd}\n"
                f"Status: {result.get('status')}\n"
                f"Exit Code: {result.get('code', -1)}\n"
                f"Stdout:\n{result.get('stdout', '')}\n"
                f"Stderr:\n{result.get('stderr', '') or result.get('message', '')}"
            )
            history.append({"role": "user", "content": result_str})

    logger.info("Direct loop hit max_turns (%d) for objective.", max_turns)
    return {"status": "incomplete", "reason": "max_turns", "turns": max_turns}
