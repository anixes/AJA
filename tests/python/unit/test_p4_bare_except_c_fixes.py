"""
P4 bare-except C-item fixes (docs/plans/P4_BARE_EXCEPT_TRIAGE.md):
- validate_chain fails-closed on malformed tool_sequence JSON
- _skill_done logs and returns False on checkpoint DB error
"""

import logging

import pytest

from aja.skills import skill_composer
from aja.skills.skill_composer import _skill_done, validate_chain


def _skill(tool_sequence: str = "[]", name: str = "s1") -> dict:
    return {
        "id": name,
        "name": name,
        "risk_level": "LOW",
        "tool_sequence": tool_sequence,
        "args_schema": {},
    }


class TestValidateChainMalformedJson:
    def test_malformed_tool_sequence_json_fails_closed(self):
        """Malformed tool_sequence JSON must be a validation failure, not skipped."""
        chain = [(_skill(tool_sequence="{not json"), "step one")]

        ok, failures = validate_chain(chain, simulate=True)

        assert ok is False
        assert any("Invalid tool_sequence JSON" in f for f in failures), failures

    def test_valid_json_still_passes_in_simulate_mode(self):
        chain = [(_skill(tool_sequence="[]"), "step one")]

        ok, failures = validate_chain(chain, simulate=True)

        assert ok is True, failures
        assert failures == []


class TestSkillDoneCheckpointError:
    def test_db_error_logs_and_returns_false(self, monkeypatch, caplog):
        """Checkpoint DB failure must log and conservatively return False."""

        def _boom(*args, **kwargs):
            raise RuntimeError("db unavailable")

        # _skill_done imports these from aja.memory.manager at call time,
        # so patching the module attributes intercepts the lookup.
        import aja.memory.manager as mem

        monkeypatch.setattr(mem, "get_memory_manager", lambda: object())
        monkeypatch.setattr(mem, "list_tables_defensive", _boom)

        with caplog.at_level(logging.ERROR, logger="aja.skills.skill_composer"):
            result = _skill_done("run-x", "skill-y")

        assert result is False
        assert any(
            "Checkpoint check failed" in r.message and r.levelno >= logging.ERROR
            for r in caplog.records
        ), caplog.text

    def test_table_missing_returns_false_without_error_log(self, monkeypatch, caplog):
        import aja.memory.manager as mem

        monkeypatch.setattr(mem, "get_memory_manager", lambda: type("M", (), {"db": object()})())
        monkeypatch.setattr(mem, "list_tables_defensive", lambda db: [])

        with caplog.at_level(logging.ERROR, logger="aja.skills.skill_composer"):
            result = _skill_done("run-x", "skill-y")

        assert result is False
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)
