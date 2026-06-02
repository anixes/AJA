import asyncio

from aja.config import DATA_DIR
from aja.gateway.remote_control import (
    execute_local_control,
    is_local_control_command,
    strip_local_control_prefix,
)


def test_local_control_prefix_detection():
    assert is_local_control_command("/pc read file") is True
    assert is_local_control_command("/local status") is True
    assert is_local_control_command("status") is False
    assert strip_local_control_prefix("/pc read file") == "read file"


def test_telegram_local_control_executes_native_tool_calls(monkeypatch):
    def fake_parse_intent(text, history, system_state=None):
        return {
            "type": "tool_calls",
            "response": "Reading that now.",
            "tool_calls": [
                {
                    "tool": "run_shell_command",
                    "args": {"cmd": "echo telegram-control"},
                }
            ],
        }

    import aja.gateway.remote_control as remote_control

    monkeypatch.setattr(remote_control, "parse_intent", fake_parse_intent)

    journal_path = DATA_DIR / "missions" / "mission_test-telegram-control.jsonl"
    try:
        reply = asyncio.run(
            execute_local_control(
                "echo telegram-control",
                mission_id="test-telegram-control",
                trace_id="tr-telegram-control",
                dry_run=True,
            )
        )

        assert "Reading that now." in reply
        assert "Local PC execution complete" in reply
        assert "run_shell_command" in reply
    finally:
        if journal_path.exists():
            journal_path.unlink()
