"""
 =============================================================================
 Unit Test: Copilot Token Storage Security (ACL hardening & export gating)
 =============================================================================
"""

import json
import logging
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from aja import copilot_auth
from aja.copilot_auth import (
    _export_token_enabled,
    _restrict_file_acl,
    copilot_device_code_login,
    invalidate_copilot_cache,
    resolve_copilot_token,
)

TOKEN = "gho_testtoken123456789"
DEVICE_PAYLOAD = {
    "verification_uri": "https://github.com/login/device",
    "user_code": "ABCD-1234",
    "device_code": "devcode123",
    "interval": 1,
}


@pytest.fixture(autouse=True)
def _default_no_export(monkeypatch):
    monkeypatch.delenv("AJA_EXPORT_COPILOT_TOKEN", raising=False)


def _clear_token_env(monkeypatch):
    for var in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen_factory(payloads):
    pending = [(_FakeResponse(p)) for p in payloads]

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        return pending.pop(0)

    return fake_urlopen


# ---------------------------------------------------------------------------
# 1. _restrict_file_acl behavior
# ---------------------------------------------------------------------------


def test_restrict_file_acl_windows_invokes_icacls(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("USERNAME", "alice")
    target = tmp_path / ".env"
    target.write_text("COPILOT_GITHUB_TOKEN=x\n", encoding="utf-8")

    recorded = {}

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        recorded["cmd"] = cmd
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(copilot_auth.subprocess, "run", fake_run)

    _restrict_file_acl(target)

    cmd = recorded["cmd"]
    assert cmd[0] == "icacls"
    assert str(target) in cmd
    assert "/inheritance:r" in cmd
    assert "/grant:r" in cmd
    assert any(a.startswith("alice") and a.endswith(":F") for a in cmd)


def test_restrict_file_acl_posix_uses_chmod_600(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    target.write_text("secret\n", encoding="utf-8")
    chmod_calls = []

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        os, "chmod", lambda p, mode: chmod_calls.append((p, mode)), raising=True
    )

    _restrict_file_acl(target)

    assert chmod_calls == [(target, 0o600)]


def test_restrict_file_acl_never_raises(caplog, tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    def boom(*args, **kwargs):  # noqa: ARG001
        raise OSError("icacls exploded")

    monkeypatch.setattr(copilot_auth.subprocess, "run", boom)
    with caplog.at_level(logging.WARNING, logger="aja.copilot_auth"):
        _restrict_file_acl(tmp_path / "missing.env")


# ---------------------------------------------------------------------------
# 2. Export gating (resolve_copilot_token path)
# ---------------------------------------------------------------------------


def test_resolve_token_not_exported_by_default(monkeypatch):
    invalidate_copilot_cache()
    _clear_token_env(monkeypatch)

    with patch.object(copilot_auth, "_try_gh_cli_token", return_value=TOKEN):
        token, source = resolve_copilot_token()

    assert token == TOKEN
    assert source == "gh auth token"
    assert os.environ.get("COPILOT_GITHUB_TOKEN") != TOKEN


def test_resolve_token_exported_when_opt_in_flag_set(monkeypatch):
    invalidate_copilot_cache()
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("AJA_EXPORT_COPILOT_TOKEN", "1")

    try:
        with patch.object(copilot_auth, "_try_gh_cli_token", return_value=TOKEN):
            token, _source = resolve_copilot_token()

        assert token == TOKEN
        assert os.environ.get("COPILOT_GITHUB_TOKEN") == TOKEN
    finally:
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)


# ---------------------------------------------------------------------------
# 3. Save path (.env write) hardening
# ---------------------------------------------------------------------------


def _login_with_mocked_flow(tmp_path, monkeypatch):
    """Drive copilot_device_code_login end-to-end with mocked HTTP + PROJECT_ROOT."""
    import aja.config as config_mod

    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        time, "sleep", lambda seconds: None  # noqa: ARG005
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen_factory([DEVICE_PAYLOAD, {"access_token": TOKEN}]),
    )
    return copilot_device_code_login()


def test_env_write_restricted_and_not_exported_by_default(tmp_path, monkeypatch):
    invalidate_copilot_cache()
    _clear_token_env(monkeypatch)

    acl_cmds = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        acl_cmds.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(copilot_auth.subprocess, "run", fake_run)

    token = _login_with_mocked_flow(tmp_path, monkeypatch)

    assert token == TOKEN
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"COPILOT_GITHUB_TOKEN={TOKEN}" in env_text
    # ACL helper ran against the exact .env path
    assert any(cmd[0] == "icacls" and str(tmp_path / ".env") in cmd for cmd in acl_cmds)
    # Token must NOT leak into child-process environment by default
    assert os.environ.get("COPILOT_GITHUB_TOKEN") != TOKEN


def test_env_write_exports_when_opt_in_flag_set(tmp_path, monkeypatch):
    invalidate_copilot_cache()
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("AJA_EXPORT_COPILOT_TOKEN", "1")
    monkeypatch.setattr(
        copilot_auth.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0)
    )

    try:
        token = _login_with_mocked_flow(tmp_path, monkeypatch)

        assert token == TOKEN
        assert os.environ.get("COPILOT_GITHUB_TOKEN") == TOKEN
    finally:
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)


# ---------------------------------------------------------------------------
# 4. Flag helper
# ---------------------------------------------------------------------------


def test_export_token_enabled_flag_semantics(monkeypatch):
    monkeypatch.delenv("AJA_EXPORT_COPILOT_TOKEN", raising=False)
    assert _export_token_enabled() is False
    monkeypatch.setenv("AJA_EXPORT_COPILOT_TOKEN", "0")
    assert _export_token_enabled() is False
    monkeypatch.setenv("AJA_EXPORT_COPILOT_TOKEN", "1")
    assert _export_token_enabled() is True
