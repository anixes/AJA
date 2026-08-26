import json
import uuid
from typing import Any, Dict, List, Optional

from aja.interface.intent_parser import parse_intent_async


MAX_TELEGRAM_REPLY_CHARS = 3500


async def _run_local_direct_loop(
    objective: str,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    mission_id: str,
    trace_id: str,
    dry_run: bool = False,
    gateway: Optional[Any] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    max_turns: int = 10,
    hooks: Optional[Any] = None,
    presenter: Optional[Any] = None,
) -> str:
    """Runs the multi-step ReAct direct tool loop for local PC control."""
    from aja.orchestration.tools.executor import ToolExecutor
    from aja.orchestration.tools.native import NativeToolRegistry
    from aja.orchestration.direct_loop import run_direct_loop, DirectLoopHooks
    from aja.gateway.presenter import AJAPresenter, NullPresenter
    from aja.llm import get_gateway_for_model
    from aja.runtime.mission_journal import MissionJournal

    journal = MissionJournal(mission_id)
    executor = ToolExecutor()
    registry = NativeToolRegistry(engine=None)

    p = presenter or (AJAPresenter() if not dry_run else NullPresenter())
    system_prompt = p.direct_system_prompt
    gw = gateway or get_gateway_for_model(model)

    working_history: List[Dict[str, str]] = []
    if history:
        for item in history:
            role = item.get("role", "user")
            c = item.get("content") or item.get("text") or ""
            if c:
                working_history.append({"role": role, "content": str(c)})
    working_history.append({"role": "user", "content": f"Please execute this task directly: {objective}"})

    outcome = await run_direct_loop(
        objective,
        gateway=gw,
        tools_registry=registry,
        executor=executor,
        system_prompt=system_prompt,
        session_history=working_history,
        max_turns=max_turns,
        model=model,
        provider=provider,
        dry_run=dry_run,
        interactive=True,
        hooks=hooks,
        trace_id_fn=lambda: trace_id,
    )

    if outcome and outcome.get("result"):
        structured = outcome["result"]
        if isinstance(structured, dict):
            res_text = structured.get("answer") or structured.get("report") or json.dumps(structured)
        else:
            res_text = str(structured)
        return _truncate(res_text)

    final_text = ""
    for m in reversed(working_history):
        if m.get("role") == "assistant":
            final_text = m.get("content") or ""
            break

    if final_text:
        return _truncate(final_text)
    return _truncate(f"Local PC execution finished ({outcome.get('status', 'done') if outcome else 'done'}).")


async def execute_local_control(
    text: str,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    mission_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    dry_run: bool = False,
    direct_loop: bool = False,
    gateway: Optional[Any] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    max_turns: int = 10,
    hooks: Optional[Any] = None,
    presenter: Optional[Any] = None,
) -> str:
    """
    Execute a Telegram-originated command through local intent parsing or multi-step agent direct loop.
    Supports single-turn tool dispatch as well as full multi-step tool execution loops.
    """
    history = history or []
    trace_id = trace_id or f"telegram-{uuid.uuid4().hex[:12]}"
    mission_id = mission_id or f"telegram-{uuid.uuid4().hex[:12]}"

    if direct_loop:
        return await _run_local_direct_loop(
            text,
            history=history,
            mission_id=mission_id,
            trace_id=trace_id,
            dry_run=dry_run,
            gateway=gateway,
            model=model,
            provider=provider,
            max_turns=max_turns,
            hooks=hooks,
            presenter=presenter,
        )

    system_state = _system_state()
    intent = await parse_intent_async(text, history, system_state=system_state)
    response = intent.get("response") or "AJA received the Telegram control request."

    intent_type = intent.get("type")
    if intent_type == "tool_calls" and intent.get("tool_calls"):
        from aja.orchestration.tools.executor import ToolExecutor
        from aja.runtime.mission_journal import MissionJournal

        journal = MissionJournal(mission_id)
        executor = ToolExecutor()
        results = await executor.dispatch_tool_calls(
            tool_calls=intent["tool_calls"],
            trace_id=trace_id,
            mission_id=mission_id,
            journal=journal,
            dry_run=dry_run,
        )
        lines = [response, "", f"Local PC execution complete. Mission: {mission_id}"]
        for result in results:
            status = "OK" if result.success else "FAILED"
            payload = result.data if result.success else result.error or result.data
            lines.append(f"[{status}] {result.tool}: {_stringify(payload)}")
        return _truncate("\n".join(lines))

    if intent_type == "control" and intent.get("command"):
        return _truncate(_control_response(intent["command"]))

    if intent_type == "goal" and intent.get("goal"):
        goal = intent.get("goal") or text
        return await _run_local_direct_loop(
            goal,
            history=history,
            mission_id=mission_id,
            trace_id=trace_id,
            dry_run=dry_run,
            gateway=gateway,
            model=model,
            provider=provider,
            max_turns=max_turns,
            hooks=hooks,
            presenter=presenter,
        )

    return _truncate(response)


def is_local_control_command(text: str) -> bool:
    lower = (text or "").strip().lower()
    return lower.startswith("/pc ") or lower.startswith("/local ") or lower.startswith("/tool ")


def strip_local_control_prefix(text: str) -> str:
    stripped = (text or "").strip()
    for prefix in ("/pc", "/local", "/tool"):
        if stripped.lower().startswith(prefix):
            return stripped[len(prefix):].strip()
    return stripped


def _system_state() -> Dict[str, Any]:
    try:
        from aja.presence.state import get_system_state
        return get_system_state()
    except Exception:
        return {}


def _control_response(command: str) -> str:
    command = command.lower()
    if command == "status":
        try:
            from aja.memory.secretary import get_aja_memory
            memory = get_aja_memory()
            active = memory.list_missions(status="ACTIVE")
            pending = memory.list_missions(status="PENDING")
            workers = memory.get_active_workers(timeout_seconds=120)
            return f"AJA status\nWorkers: {len(workers)} active\nMissions: {len(active)} active, {len(pending)} pending"
        except Exception as exc:
            return f"Status check failed: {exc}"
    if command == "doctor":
        try:
            from aja.utils.diagnostics import run_diagnostics
            checks = run_diagnostics()
            return "Doctor\n" + "\n".join(f"{'OK' if ok else 'WARN'} {name}: {msg}" for name, ok, msg in checks)
        except Exception as exc:
            return f"Doctor check failed: {exc}"
    return f"Control command '{command}' is not available from Telegram local control."


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _truncate(text: str) -> str:
    if len(text) <= MAX_TELEGRAM_REPLY_CHARS:
        return text
    return text[: MAX_TELEGRAM_REPLY_CHARS - 80].rstrip() + "\n\n... output trimmed for Telegram."
