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

    # remote_control now awaits the async parser (never blocks the loop on a
    # sync LLM roundtrip), so patch the async twin.
    async def fake_parse_intent_async(text, history, system_state=None):
        return fake_parse_intent(text, history, system_state=system_state)

    monkeypatch.setattr(remote_control, "parse_intent_async", fake_parse_intent_async)

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


def test_telegram_local_control_executes_direct_loop_for_goals(monkeypatch):
    import aja.gateway.remote_control as remote_control

    async def fake_parse_intent_async(text, history, system_state=None):
        return {
            "type": "goal",
            "goal": "inspect project files",
            "response": "Starting direct execution.",
        }

    monkeypatch.setattr(remote_control, "parse_intent_async", fake_parse_intent_async)

    class FakeGateway:
        def __init__(self):
            self.turn = 0

        async def chat(self, model=None, prompt=None, system=None, tools=None):
            self.turn += 1
            if self.turn == 1:
                return "```bash\necho found_file_123\n```"
            return "Found file: found_file_123. Task complete."

    reply = asyncio.run(
        execute_local_control(
            "inspect project files",
            gateway=FakeGateway(),
            dry_run=True,
        )
    )

    assert "Found file: found_file_123. Task complete." in reply


def test_telegram_local_control_direct_loop_flag(monkeypatch):
    class FakeGateway:
        async def chat(self, model=None, prompt=None, system=None, tools=None):
            return "Direct execution completed successfully, Sir."

    reply = asyncio.run(
        execute_local_control(
            "run check",
            gateway=FakeGateway(),
            direct_loop=True,
            dry_run=True,
        )
    )

    assert "Direct execution completed successfully, Sir." in reply

