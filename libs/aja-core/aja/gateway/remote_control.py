import json
import uuid
from typing import Any, Dict, List, Optional

from aja.interface.intent_parser import parse_intent


MAX_TELEGRAM_REPLY_CHARS = 3500


async def execute_local_control(
    text: str,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    mission_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    dry_run: bool = False,
) -> str:
    """
    Execute a Telegram-originated command through the same local intent/tool path as CLI chat.
    This is intentionally not a raw shell bridge: the intent parser must map the request into
    trusted NativeToolRegistry tool calls, and ActivityRuntime applies permission policy.
    """
    history = history or []
    trace_id = trace_id or f"telegram-{uuid.uuid4().hex[:12]}"
    mission_id = mission_id or f"telegram-{uuid.uuid4().hex[:12]}"

    system_state = _system_state()
    intent = parse_intent(text, history, system_state=system_state)
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
        return _truncate(
            f"{response}\n\nFor full swarm execution from Telegram, send `/swarm {intent['goal']}`. "
            "For direct local PC control, send `/pc <request>`."
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
