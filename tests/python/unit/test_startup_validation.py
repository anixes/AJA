"""
Unit tests for aja.utils.startup_checks (bootstrap configuration validation).
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libs" / "aja-core"))

from aja.utils.startup_checks import (  # noqa: E402
    CheckResult,
    check_baton_security,
    check_data_dir_writable,
    check_model_api_keys,
    check_retention,
    run_startup_checks,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Strip all model/key/baton env vars so tests are deterministic."""
    for var in (
        "AJA_PLANNER_MODEL",
        "AJA_WORKER_MODEL",
        "AJA_CRITIC_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "AJA_BATON_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)


def _by_name(results, name):
    return [r for r in results if r.name == name]


class TestModelApiKeys:
    def test_missing_key_is_error(self, monkeypatch):
        monkeypatch.setenv("AJA_PLANNER_MODEL", "openai:gpt-4o")
        results = check_model_api_keys(config={})
        planner = _by_name(results, "Model Key (planner)")
        assert planner and planner[0].severity == "error"
        assert "OPENAI_API_KEY" in planner[0].detail

    def test_present_key_is_ok(self, monkeypatch):
        monkeypatch.setenv("AJA_PLANNER_MODEL", "openai:gpt-4o")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        results = check_model_api_keys(config={})
        planner = _by_name(results, "Model Key (planner)")
        assert planner and planner[0].severity == "ok"

    def test_google_gemini_fallback_key(self, monkeypatch):
        monkeypatch.setenv("AJA_PLANNER_MODEL", "google:gemini-2.0-flash")
        monkeypatch.setenv("GEMINI_API_KEY", "g-key")  # only GEMINI set
        results = check_model_api_keys(config={})
        assert _by_name(results, "Model Key (planner)")[0].severity == "ok"

    def test_google_missing_both_keys(self, monkeypatch):
        monkeypatch.setenv("AJA_PLANNER_MODEL", "google:gemini-2.0-flash")
        results = check_model_api_keys(config={})
        assert _by_name(results, "Model Key (planner)")[0].severity == "error"

    def test_critic_defaults_to_planner_model(self, monkeypatch):
        monkeypatch.setenv("AJA_PLANNER_MODEL", "anthropic:claude-x")
        results = check_model_api_keys(config={})
        critic = _by_name(results, "Model Key (critic)")
        # Critic falls back to planner's model -> same missing ANTHROPIC_API_KEY error
        assert critic and critic[0].severity == "error"

    def test_unknown_provider_warns(self, monkeypatch):
        monkeypatch.setenv("AJA_PLANNER_MODEL", "weirdco:model-x")
        results = check_model_api_keys(config={})
        assert _by_name(results, "Model Key (planner)")[0].severity == "warning"

    def test_local_llama_cpp_needs_no_key(self, monkeypatch):
        monkeypatch.setenv("AJA_PLANNER_MODEL", "llama_cpp:llama-3")
        results = check_model_api_keys(config={})
        assert _by_name(results, "Model Key (planner)")[0].severity == "ok"

    def test_worker_role_checked(self, monkeypatch):
        monkeypatch.setenv("AJA_WORKER_MODEL", "openrouter:m/x")
        results = check_model_api_keys(config={})
        worker = _by_name(results, "Model Key (worker)")
        assert worker and worker[0].severity == "error"


class TestBatonSecurity:
    def test_secret_set_is_ok(self, monkeypatch):
        monkeypatch.setenv("AJA_BATON_SECRET", "s3cr3t")
        r = check_baton_security(config={})
        assert r.severity == "ok"

    def test_unset_with_remote_endpoint_warns(self, monkeypatch):
        config = {"runtime": {"baton_endpoint_url": "https://fleet.example.com/batons"}}
        r = check_baton_security(config=config)
        assert r.severity == "warning"
        assert "baton_endpoint_url" in r.detail

    def test_unset_without_endpoints_skips_quietly(self, monkeypatch):
        r = check_baton_security(config={"project_name": "AJA"})
        assert r.severity == "ok"
        assert "No remote baton endpoints" in r.detail

    def test_trivial_json_read_path(self, tmp_path, monkeypatch):
        cfg = tmp_path / "aja.json"
        cfg.write_text(
            '{"baton": {"remote_host": "h.example.com"}}', encoding="utf-8"
        )
        monkeypatch.setattr(
            "aja.utils.startup_checks._config_path", lambda: cfg
        )
        r = check_baton_security(config=None)
        assert r.severity == "warning"


class TestRetentionSanity:
    def test_valid_ttls_ok(self):
        config = type("C", (), {})()
        config.model_dump = lambda: {"scheduler": {"task_ttl_days": 30}}
        r = check_retention(config=config)
        assert r.severity == "ok"

    def test_invalid_ttl_error(self):
        config = type("C", (), {})()
        config.model_dump = lambda: {"scheduler": {"task_ttl_days": -5}}
        r = check_retention(config=config)
        assert r.severity == "error"

    def test_non_int_ttl_error(self):
        config = type("C", (), {})()
        config.model_dump = lambda: {"retention_hours": "soon"}
        assert check_retention(config=config).severity == "error"

    def test_no_knobs_skips(self):
        config = type("C", (), {})()
        config.model_dump = lambda: {"project_name": "AJA"}
        r = check_retention(config=config)
        assert r.severity == "ok"


class TestDataDirWritability:
    @pytest.mark.skipif(os.name == "nt", reason="chmod-based denial is POSIX-only")
    def test_unwritable_dir_errors_posix(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)  # read-only
        try:
            r = check_data_dir_writable(data_dir=locked / "sub")
            assert r.severity == "error"
        finally:
            locked.chmod(0o700)

    @pytest.mark.skipif(os.name != "nt", reason="Windows-specific behavior")
    def test_windows_bad_path_appropriately_handled(self, tmp_path):
        # On Windows use an invalid path segment to force failure deterministically
        bad = tmp_path / ("bad<" + "x" * 200)
        r = check_data_dir_writable(data_dir=bad)
        assert r.severity in ("error",)

    def test_writable_tmpdir_ok(self, tmp_path):
        target = tmp_path / "data" / "nested"
        r = check_data_dir_writable(data_dir=target)
        assert r.severity == "ok"
        assert target.exists()


class TestRunStartupChecks:
    def test_returns_check_results_fast(self):
        results = run_startup_checks(config={"project_name": "AJA"})
        assert len(results) >= 4
        assert all(isinstance(r, CheckResult) for r in results)
        assert {r.severity for r in results} <= {"error", "warning", "ok"}

    def test_all_ok_with_keys_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AJA_PLANNER_MODEL", "openai:gpt-4o")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("AJA_BATON_SECRET", "secret")
        results = run_startup_checks(config={"project_name": "AJA"})
        assert not any(r.severity == "error" for r in results)
