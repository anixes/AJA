"""
Regression tests for runtime CommandGuard enforcement in skill execution:
- Shell-replay steps inside skills must pass CommandGuard before execution
  (aja/skills/skill_executor.py)
- Catastrophic commands abort the skill run without executing.
- 'ask'-class commands deny by default unless allow_ask_steps=True.
- Classification outcomes are logged for auditability.
"""

import json
import logging
import uuid

import pytest

from aja.skills import skill_executor
from aja.skills.skill_executor import (
    classify_skill_step,
    execute_skill,
    _execute_step,
)


def _shell_step(command: str) -> dict:
    return {"tool_name": "shell", "args_schema": {"command": command}}


def _run_id() -> str:
    # ToolGuard coalesces on run_id — unique ids keep runs independent.
    return f"guard-{uuid.uuid4().hex[:12]}"


class _InvokeRecorder:
    """Records every _invoke_tool call; never actually executes anything."""

    def __init__(self):
        self.calls = []

    def __call__(self, tool_name, args):
        self.calls.append((tool_name, args))
        return json.dumps({"simulated": True, "tool": tool_name}), None


@pytest.fixture()
def recorder(monkeypatch):
    rec = _InvokeRecorder()
    monkeypatch.setattr(skill_executor, "_invoke_tool", rec)
    return rec


class TestRuntimeCommandGuard:
    def test_catastrophic_command_aborts_without_execution(self, recorder):
        """A skill step replaying 'rm -rf /' must be aborted before any execution."""
        ok, result, error = _execute_step(
            _run_id(), _shell_step("rm -rf /"), step_index=0
        )

        assert ok is False
        assert result is None
        assert error is not None
        assert "deny" in error.lower() or "blocked" in error.lower()
        assert recorder.calls == [], "denied step must never reach tool invocation"

    def test_catastrophic_command_structured_failure_via_execute_skill(
        self, recorder, monkeypatch
    ):
        """execute_skill returns False and journals a structured failure naming the step."""

        class FakeTracker:
            def __init__(self):
                self.events = []

            def log_event(self, event, payload):
                self.events.append((event, payload))

        tracker = FakeTracker()
        monkeypatch.setattr(skill_executor, "_refresh_last_used", lambda sid: None)
        monkeypatch.setattr(skill_executor, "_update_skill_metrics", lambda s, success: None)
        monkeypatch.setattr(skill_executor, "_load_completed_steps", lambda s, r: {})
        monkeypatch.setattr(skill_executor, "_checkpoint_step", lambda *a, **k: None)
        monkeypatch.setattr(skill_executor, "_clear_checkpoints", lambda s, r: None)

        skill = {
            "id": "skill-evil",
            "name": "evil-skill",
            "risk_level": "LOW",
            "prerequisites": "[]",
            "tool_sequence": json.dumps([_shell_step("rm -rf /")]),
        }

        completed = execute_skill(
            skill,
            task_id=1,
            run_id=_run_id(),
            objective="clean disk",
            tracker=tracker,
        )

        assert completed is False
        assert recorder.calls == []
        failures = [e for e, _ in tracker.events if e == "SKILL_EXECUTION_FAILED"]
        assert failures, "structured SKILL_EXECUTION_FAILED event expected"
        payload = next(p for e, p in tracker.events if e == "SKILL_EXECUTION_FAILED")
        assert payload.get("failed_step") == 0
        assert "deny" in str(payload.get("error", "")).lower()

    def test_benign_command_executes_normally(self, recorder):
        ok, result, error = _execute_step(
            _run_id(), _shell_step("echo hello"), step_index=0
        )

        assert ok is True
        assert error is None
        assert len(recorder.calls) == 1
        assert recorder.calls[0][0] == "shell"
        assert recorder.calls[0][1]["command"] == "echo hello"

    def test_ask_class_command_denied_by_default(self, recorder):
        ok, result, error = _execute_step(
            _run_id(), _shell_step("taskkill /F /IM x"), step_index=0
        )

        assert ok is False
        assert "ask" in error.lower()
        assert recorder.calls == []

    def test_ask_class_command_permitted_with_explicit_opt_in(self, recorder):
        ok, _, error = _execute_step(
            _run_id(),
            _shell_step("taskkill /F /IM x"),
            step_index=0,
            allow_ask_steps=True,
        )

        assert ok is True
        assert error is None
        assert len(recorder.calls) == 1

    def test_non_shell_steps_skip_guard(self, recorder):
        step = {"tool_name": "send_email", "args_schema": {"to": "a@b.c"}}
        ok, _, error = _execute_step(_run_id(), step, step_index=0)

        assert ok is True
        assert error is None
        assert len(recorder.calls) == 1


class TestClassificationAuditLog:
    def test_deny_outcome_is_logged(self, recorder, caplog):
        with caplog.at_level(logging.INFO, logger="aja.skills.skill_executor"):
            _execute_step("run-log-1", _shell_step("rm -rf /"), step_index=0)

        assert "CommandGuard" in caplog.text
        assert "deny" in caplog.text
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_allow_outcome_is_logged(self, recorder, caplog):
        with caplog.at_level(logging.INFO, logger="aja.skills.skill_executor"):
            _execute_step("run-log-2", _shell_step("echo hello"), step_index=0)

        assert "decision=allow" in caplog.text

    def test_classify_skill_step_returns_decision_and_reasons(self):
        # allow → no abort needed
        classification, error = classify_skill_step(_shell_step("echo hello"), 0)
        assert classification is None or classification["decision"] == "allow"
        assert error is None

        classification, error = classify_skill_step(
            _shell_step("taskkill /F /IM x"), 1
        )
        assert classification is None or classification["decision"] != "deny"
        assert error is not None and "ask" in error.lower()

    def test_classify_skill_step_deny_reports_reasons(self, caplog):
        with caplog.at_level(logging.INFO, logger="aja.skills.skill_executor"):
            classification, error = classify_skill_step(_shell_step("rm -rf /"), 0)
        assert error is not None and "deny" in error.lower()
        assert "Root filesystem destructive deletion" in caplog.text

    def test_non_shell_step_returns_no_classification(self):
        classification, error = classify_skill_step(
            {"tool_name": "send_email", "args_schema": {"to": "a@b.c"}}, 0
        )
        assert classification is None
        assert error is None

