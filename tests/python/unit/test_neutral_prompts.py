"""Neutral-prompt mode tests: persona swap via env + config field."""

import importlib
import sys

import pytest


@pytest.fixture
def presenter():
    import aja.gateway.presenter as mod

    importlib.reload(mod)
    yield mod
    importlib.reload(mod)


def test_default_is_persona(presenter, monkeypatch):
    monkeypatch.delenv("AJA_NEUTRAL_PROMPTS", raising=False)
    from aja.gateway.presenter import AJAPresenter

    p = AJAPresenter()
    s = p.direct_system_prompt
    assert "Assistant of Joint Agents" in s
    assert "Sir" in s or "secretary" in s.lower()


def test_env_flag_swaps_to_neutral(presenter, monkeypatch):
    monkeypatch.setenv("AJA_NEUTRAL_PROMPTS", "1")
    from aja.gateway.presenter import AJAPresenter

    p = AJAPresenter()
    s = p.direct_system_prompt
    assert "Sir" not in s and "secretary" not in s.lower()
    assert "AI agent operating" in s


def test_config_field_swaps_to_neutral(presenter, monkeypatch):
    monkeypatch.setenv("AJA_NEUTRAL_PROMPTS", "")
    fake_cfg = type("C", (), {})()
    fake_cfg.swarm_settings = type("S", (), {"neutral_prompts": True})()
    import aja.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG", fake_cfg, raising=False)
    assert presenter._neutral_prompts_requested() is True
    from aja.gateway.presenter import AJAPresenter

    p = AJAPresenter()
    s = p.direct_system_prompt
    assert "Sir" not in s and "AI agent operating" in s
