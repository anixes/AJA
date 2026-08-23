"""
 =============================================================================
 Unit Test: Copilot Keyring Integration (resolution order, dual-write,
 migration helper) — keyring module fully mocked.
 =============================================================================
"""

import sys
import types

import pytest

from aja import copilot_auth
from aja.copilot_auth import (
    copilot_device_code_login,
    invalidate_copilot_cache,
    migrate_token_to_keyring,
    resolve_copilot_token,
)

TOKEN = "gho_krtesttoken123456789"
DEVICE_PAYLOAD = {
    "verification_uri": "https://github.com/login/device",
    "user_code": "ABCD-1234",
    "device_code": "devcode123",
    "interval": 1,
}


class FakeKeyring:
    """In-memory stand-in for the keyring module."""

    def __init__(self, store=None, raise_on_get=False, raise_on_set=False):
        self.store = dict(store or {})
        self.raise_on_get = raise_on_get
        self.raise_on_set = raise_on_set
        self.set_calls = []

    def get_password(self, service, username):
        if self.raise_on_get:
            raise RuntimeError("headless keychain")
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        if self.raise_on_set:
            raise RuntimeError("read-only keychain")
        self.set_calls.append((service, username, password))
        self.store[(service, username)] = password


def _install_fake_keyring(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "keyring", fake)


def _clear_token_env(monkeypatch):
    for var in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_cache():
    invalidate_copilot_cache()
    yield
    invalidate_copilot_cache()


# ---------------------------------------------------------------------------
# 1. Resolution order
# ---------------------------------------------------------------------------


def test_resolution_prefers_keyring_over_env(monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "gho_env_fallback_token")
    _install_fake_keyring(
        monkeypatch, FakeKeyring({("AJA", "copilot"): TOKEN})
    )
    monkeypatch.setattr(copilot_auth, "_try_gh_cli_token", lambda: None)

    token, source = resolve_copilot_token()

    assert token == TOKEN
    assert source == "keyring"


def test_resolution_falls_back_to_env_when_keyring_none(monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", TOKEN)
    _install_fake_keyring(monkeypatch, FakeKeyring(store={}))

    token, source = resolve_copilot_token()

    assert token == TOKEN
    assert source == "COPILOT_GITHUB_TOKEN"


def test_resolution_survives_keyring_exception(monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", TOKEN)
    _install_fake_keyring(
        monkeypatch, FakeKeyring(raise_on_get=True)
    )

    token, source = resolve_copilot_token()

    assert token == TOKEN
    assert source == "COPILOT_GITHUB_TOKEN"


# ---------------------------------------------------------------------------
# 2. Dual-write on login save
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        import json

        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _login_with_mocked_flow(tmp_path, monkeypatch):
    import time

    import aja.config as config_mod

    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)  # noqa: ARG005

    payloads = [_FakeResponse(DEVICE_PAYLOAD), _FakeResponse({"access_token": TOKEN})]
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: payloads.pop(0)
    )
    return copilot_device_code_login()


def test_login_dual_writes_keyring_and_env(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    fake = FakeKeyring()
    _install_fake_keyring(monkeypatch, fake)

    # Neutralize the ACL subprocess call (icacls) in the test sandbox.
    monkeypatch.setattr(copilot_auth.subprocess, "run", lambda *a, **k: None)

    token = _login_with_mocked_flow(tmp_path, monkeypatch)

    assert token == TOKEN
    assert fake.set_calls == [("AJA", "copilot", TOKEN)]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"COPILOT_GITHUB_TOKEN={TOKEN}" in env_text


def test_login_env_write_succeeds_even_if_keyring_raises(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    _install_fake_keyring(
        monkeypatch, FakeKeyring(raise_on_set=True)
    )
    monkeypatch.setattr(copilot_auth.subprocess, "run", lambda *a, **k: None)

    token = _login_with_mocked_flow(tmp_path, monkeypatch)

    assert token == TOKEN
    assert f"COPILOT_GITHUB_TOKEN={TOKEN}" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 3. migrate_token_to_keyring
# ---------------------------------------------------------------------------


def test_migrate_copies_env_token_when_keyring_empty(tmp_path, monkeypatch):
    import aja.config as config_mod

    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        f"SOMETHING=else\nCOPILOT_GITHUB_TOKEN={TOKEN}\n", encoding="utf-8"
    )
    fake = FakeKeyring()
    _install_fake_keyring(monkeypatch, fake)

    assert migrate_token_to_keyring() is True
    assert fake.store[("AJA", "copilot")] == TOKEN


def test_migrate_noop_when_keyring_already_has_token(tmp_path, monkeypatch):
    import aja.config as config_mod

    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        f"COPILOT_GITHUB_TOKEN={TOKEN}\n", encoding="utf-8"
    )
    fake = FakeKeyring({("AJA", "copilot"): "gho_existing"})
    _install_fake_keyring(monkeypatch, fake)

    assert migrate_token_to_keyring() is False
    assert fake.store[("AJA", "copilot")] == "gho_existing"
    assert fake.set_calls == []


def test_migrate_returns_false_when_neither_has_token(tmp_path, monkeypatch):
    import aja.config as config_mod

    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("OTHER=value\n", encoding="utf-8")
    fake = FakeKeyring()
    _install_fake_keyring(monkeypatch, fake)

    assert migrate_token_to_keyring() is False
    assert fake.set_calls == []
