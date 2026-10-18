"""Per-platform gateway authorization tests (telegram/discord/slack).

Covers aja.gateway.auth.is_user_authorized semantics:
- allowlist match allows
- configured-token-without-allowlist denies (fail-safe)
- no-token-no-allowlist allows (local-only dev setup)
- comma-separated multi-ID matching (discord/slack)
- "*" wildcard = explicit allow-all
Plus regression coverage of the orchestrator's Telegram delegation.
"""

import pytest

import aja.config as aja_config
from aja.gateway.auth import get_platform_posture, is_user_authorized

PLATFORMS = ["telegram", "discord", "slack"]

ALLOWLIST_ENVS = {
    "telegram": ["TELEGRAM_ALLOWED_USER_ID"],
    "discord": ["DISCORD_ALLOWED_USER_IDS", "DISCORD_ALLOWED_USER_ID"],
    "slack": ["SLACK_ALLOWED_USER_IDS", "SLACK_ALLOWED_USER_ID"],
}

TOKEN_ENVS = {
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"],
    "discord": ["DISCORD_BOT_TOKEN", "DISCORD_TOKEN"],
    "slack": ["SLACK_BOT_TOKEN", "SLACK_TOKEN"],
}


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch):
    """Hermetic auth environment: clear every allowlist/token env AND the
    aja.config import-time snapshot used by the fallback chain."""
    for env_names in list(ALLOWLIST_ENVS.values()) + list(TOKEN_ENVS.values()):
        for name in env_names:
            monkeypatch.delenv(name, raising=False)
            monkeypatch.setattr(aja_config, name, None, raising=False)


def _set_allowlist(monkeypatch, platform, value):
    monkeypatch.setenv(ALLOWLIST_ENVS[platform][0], value)


def _set_token(monkeypatch, platform, value="x-test-token"):
    monkeypatch.setenv(TOKEN_ENVS[platform][0], value)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_allowlist_match_allows(monkeypatch, platform):
    _set_token(monkeypatch, platform)
    _set_allowlist(monkeypatch, platform, "42")
    assert is_user_authorized(platform, "42") is True


@pytest.mark.parametrize("platform", PLATFORMS)
def test_allowlist_mismatch_denies(monkeypatch, platform):
    _set_token(monkeypatch, platform)
    _set_allowlist(monkeypatch, platform, "42")
    assert is_user_authorized(platform, "999") is False


@pytest.mark.parametrize("platform", PLATFORMS)
def test_token_without_allowlist_denies_failsafe(monkeypatch, platform):
    """Fail-safe: bot token configured + NO allowlist => remote users DENIED."""
    _set_token(monkeypatch, platform)
    assert is_user_authorized(platform, "42") is False
    assert is_user_authorized(platform, "") is False


@pytest.mark.parametrize("platform", PLATFORMS)
def test_no_token_no_allowlist_allows_local_only(monkeypatch, platform):
    """Local-only dev setup (no token anywhere) must NOT be bricked."""
    assert is_user_authorized(platform, "anyone") is True


@pytest.mark.parametrize("platform", ["discord", "slack"])
def test_comma_separated_multi_id_matching(monkeypatch, platform):
    _set_token(monkeypatch, platform)
    _set_allowlist(monkeypatch, platform, "111, 222 ,333")
    assert is_user_authorized(platform, "111") is True
    assert is_user_authorized(platform, "222") is True
    assert is_user_authorized(platform, "333") is True
    assert is_user_authorized(platform, "444") is False


@pytest.mark.parametrize("platform", ["discord", "slack"])
def test_comma_list_with_wildcard_entry_is_not_treated_as_wildcard(monkeypatch, platform):
    """Only a standalone '*' is the wildcard opt-in; '1,*' stays an ID list."""
    _set_token(monkeypatch, platform)
    _set_allowlist(monkeypatch, platform, "1,*")
    assert is_user_authorized(platform, "1") is True


@pytest.mark.parametrize("platform", PLATFORMS)
def test_star_wildcard_allows_all(monkeypatch, platform):
    _set_token(monkeypatch, platform)
    _set_allowlist(monkeypatch, platform, "*")
    assert is_user_authorized(platform, "anything") is True
    assert is_user_authorized(platform, "99999") is True


def test_secondary_token_env_counts_as_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "legacy-token")
    monkeypatch.setenv("DISCORD_TOKEN", "legacy-discord")
    monkeypatch.setenv("SLACK_TOKEN", "legacy-slack")
    for platform in PLATFORMS:
        assert is_user_authorized(platform, "42") is False


def test_posture_report_states():
    token_set, allowlist_set = get_platform_posture("telegram")
    assert token_set is False and allowlist_set is False  # cleaned env fixture

    import os
    os.environ["TELEGRAM_BOT_TOKEN"] = "t"
    try:
        assert get_platform_posture("telegram") == (True, False)
        os.environ["TELEGRAM_ALLOWED_USER_ID"] = "7"
        assert get_platform_posture("telegram") == (True, True)
    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_ALLOWED_USER_ID", None)


# ── Orchestrator delegation regression ──────────────────────────────────────


def _make_event(user_id="42"):
    from aja.gateway.base import MessageEvent, MessageType

    return MessageEvent(
        platform="telegram",
        chat_id="999",
        user_id=user_id,
        message_type=MessageType.TEXT,
        text="status",
    )


def _make_gateway():
    from aja.gateway.orchestrator import UnifiedGateway

    gw = object.__new__(UnifiedGateway)
    gw._open_gateway_warned = False
    return gw


def test_orchestrator_telegram_delegation_match(monkeypatch):
    _set_token(monkeypatch, "telegram")
    _set_allowlist(monkeypatch, "telegram", "42")
    assert _make_gateway()._is_telegram_user_authorized(_make_event("42")) is True


def test_orchestrator_telegram_fail_safe_deny_with_token(monkeypatch):
    _set_token(monkeypatch, "telegram")
    assert _make_gateway()._is_telegram_user_authorized(_make_event("42")) is False


def test_orchestrator_telegram_local_only_no_token(monkeypatch):
    assert _make_gateway()._is_telegram_user_authorized(_make_event("42")) is True


def test_orchestrator_warns_once_then_stays_quiet(monkeypatch):
    import aja.gateway.orchestrator as orch_mod

    _set_token(monkeypatch, "telegram")
    gw = _make_gateway()
    warnings = []
    monkeypatch.setattr(orch_mod.logger, "warning", lambda msg, *a, **k: warnings.append(msg))

    assert gw._is_telegram_user_authorized(_make_event("42")) is False
    assert len(warnings) == 1
    # Second denial: flag already set, no duplicate warning.
    assert gw._is_telegram_user_authorized(_make_event("43")) is False
    assert len(warnings) == 1


# ── Adapter wiring smoke ────────────────────────────────────────────────────


def test_adapters_expose_reject_metric_and_import_cleanly():
    from aja.gateway.adapters.discord_adapter import DiscordAdapter
    from aja.gateway.adapters.slack_adapter import SlackAdapter

    d = DiscordAdapter({"token": "x"})
    s = SlackAdapter({"token": "x"})
    assert d.metrics["events_rejected"] == 0
    assert s.metrics["events_rejected"] == 0
