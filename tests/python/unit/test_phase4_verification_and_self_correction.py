"""
test_phase4_verification_and_self_correction.py
===============================================
Comprehensive test suite for Phase 4: Agent Self-Correction & Verification Ladder Hardening.
Tests:
- VerificationConfig Pydantic schema validation & bounds
- Multi-format Tier 1 syntax (Python, JSON, TOML, YAML, null-bytes, zero-bytes)
- Dynamic test target discovery (discover_test_targets)
- Pytest exit code 5 soft-pass handling
- Deliverables verification (verify_deliverables)
- Oscillation detection & to_feedback_prompt warning injection
- Direct loop self-correction integration with oscillation detection
"""

import asyncio
from pathlib import Path
import pytest
from pydantic import ValidationError

from aja.config_schema import VerificationConfig, SwarmSettings
from aja.orchestration.verification_runner import (
    VerificationResult,
    verify_file_syntax,
    verify_syntax,
    verify_syntax_in_workspace,
    verify_deliverables,
    discover_test_targets,
    run_targeted_tests_sync,
    run_targeted_tests,
    run_verification_ladder,
)
from aja.orchestration.direct_loop import (
    run_direct_loop,
    _compute_error_signature,
    _find_missing_deliverables,
)


# ============================================================================
# 1. VerificationConfig Schema & Validation Tests
# ============================================================================

def test_verification_config_defaults():
    cfg = VerificationConfig()
    assert cfg.max_verification_retries == 3
    assert cfg.tier1_syntax_enabled is True
    assert cfg.tier2_tests_enabled is True
    assert cfg.tier3_git_diff_enabled is True
    assert cfg.test_runner_timeout_seconds == 60.0
    assert cfg.auto_discover_tests is True
    assert cfg.flaky_retry_limit == 3
    assert cfg.tier1_max_files_scan == 150
    assert cfg.destructive_wipeout_threshold == 500


def test_verification_config_bounds_valid():
    cfg = VerificationConfig(
        max_verification_retries=5,
        test_runner_timeout_seconds=120.0,
        flaky_retry_limit=4,
        destructive_wipeout_threshold=100,
    )
    assert cfg.max_verification_retries == 5
    assert cfg.test_runner_timeout_seconds == 120.0
    assert cfg.flaky_retry_limit == 4
    assert cfg.destructive_wipeout_threshold == 100


def test_verification_config_bounds_invalid():
    with pytest.raises(ValidationError):
        VerificationConfig(max_verification_retries=0)

    with pytest.raises(ValidationError):
        VerificationConfig(test_runner_timeout_seconds=0)

    with pytest.raises(ValidationError):
        VerificationConfig(test_runner_timeout_seconds=-10.0)

    with pytest.raises(ValidationError):
        VerificationConfig(flaky_retry_limit=0)

    with pytest.raises(ValidationError):
        VerificationConfig(destructive_wipeout_threshold=5)


def test_swarm_settings_has_verification_config():
    settings = SwarmSettings()
    assert hasattr(settings, "verification")
    assert isinstance(settings.verification, VerificationConfig)
    assert settings.verification.max_verification_retries == 3


# ============================================================================
# 2. Multi-Format Tier 1 Syntax Verification Tests
# ============================================================================

def test_verify_file_syntax_python(tmp_path: Path):
    valid_py = tmp_path / "valid.py"
    valid_py.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    ok, err = verify_file_syntax(valid_py)
    assert ok is True
    assert err is None

    broken_py = tmp_path / "broken.py"
    broken_py.write_text("def broken(:\n    pass\n", encoding="utf-8")
    ok, err = verify_file_syntax(broken_py)
    assert ok is False
    assert "invalid syntax" in (err or "")


def test_verify_file_syntax_json(tmp_path: Path):
    valid_json = tmp_path / "data.json"
    valid_json.write_text('{"name": "test", "active": true, "values": [1, 2, 3]}', encoding="utf-8")
    ok, err = verify_file_syntax(valid_json)
    assert ok is True
    assert err is None

    broken_json = tmp_path / "broken.json"
    broken_json.write_text('{"name": "test", "active": true,}', encoding="utf-8")
    ok, err = verify_file_syntax(broken_json)
    assert ok is False
    assert "JSON syntax error" in (err or "")


def test_verify_file_syntax_toml(tmp_path: Path):
    valid_toml = tmp_path / "config.toml"
    valid_toml.write_text('[project]\nname = "aja"\nversion = "1.0"\n', encoding="utf-8")
    ok, err = verify_file_syntax(valid_toml)
    assert ok is True
    assert err is None

    broken_toml = tmp_path / "broken.toml"
    broken_toml.write_text('[project\nname = "unclosed bracket"\n', encoding="utf-8")
    ok, err = verify_file_syntax(broken_toml)
    assert ok is False
    assert "TOML syntax error" in (err or "")


def test_verify_file_syntax_yaml(tmp_path: Path):
    valid_yaml = tmp_path / "manifest.yaml"
    valid_yaml.write_text("app:\n  name: aja\n  port: 8080\n", encoding="utf-8")
    ok, err = verify_file_syntax(valid_yaml)
    assert ok is True
    assert err is None

    broken_yaml = tmp_path / "broken.yaml"
    broken_yaml.write_text("app:\n  name: [unclosed list\n  port: 8080\n", encoding="utf-8")
    ok, err = verify_file_syntax(broken_yaml)
    assert ok is False
    assert "YAML syntax error" in (err or "") or "error" in (err or "").lower()


def test_verify_file_syntax_corrupt_and_zero_byte(tmp_path: Path):
    zero_file = tmp_path / "empty.py"
    zero_file.write_bytes(b"")
    ok, err = verify_file_syntax(zero_file)
    assert ok is False
    assert "Zero-byte" in (err or "")

    null_byte_file = tmp_path / "corrupt.json"
    null_byte_file.write_bytes(b'{"key": "val\x00corrupt"}')
    ok, err = verify_file_syntax(null_byte_file)
    assert ok is False
    assert "null bytes" in (err or "")

    corrupt_utf8 = tmp_path / "bad_utf8.py"
    corrupt_utf8.write_bytes(b"\xff\xfe\x00\x00")
    ok, err = verify_file_syntax(corrupt_utf8)
    assert ok is False


def test_verify_syntax_workspace_filtering(tmp_path: Path):
    # Create valid multi-format files
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.json").write_text('{"ok": true}', encoding="utf-8")
    (tmp_path / "c.toml").write_text('key = "value"', encoding="utf-8")
    (tmp_path / "notes.txt").write_text("plain text", encoding="utf-8")

    # Create ignored folder
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "bad.py").write_text("def broken(:", encoding="utf-8")

    res = verify_syntax_in_workspace(root=tmp_path)
    assert res.passed is True
    assert res.check_type == "syntax_and_hygiene"
    assert "passed" in res.summary


# ============================================================================
# 3. Dynamic Test Discovery Tests
# ============================================================================

def test_discover_test_targets(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    foo_file = src_dir / "foo.py"
    foo_file.write_text("def foo(): return 1", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_foo = tests_dir / "test_foo.py"
    test_foo.write_text("def test_foo(): assert True", encoding="utf-8")

    # Discover for foo_file
    discovered = discover_test_targets([foo_file], workspace_root=tmp_path)
    assert len(discovered) == 1
    assert "tests/test_foo.py" in discovered[0]

    # If file itself is a test file, it should discover itself
    discovered_self = discover_test_targets([test_foo], workspace_root=tmp_path)
    assert len(discovered_self) == 1
    assert "tests/test_foo.py" in discovered_self[0]

    # Out of workspace root check
    outside = Path("/some/outside/path/foo.py")
    discovered_out = discover_test_targets([outside], workspace_root=tmp_path)
    assert len(discovered_out) == 0


# ============================================================================
# 4. Pytest Exit Code 5 Soft-Pass Handling
# ============================================================================

def test_run_targeted_tests_exit_code_5_soft_pass(tmp_path: Path):
    empty_test_file = tmp_path / "test_empty.py"
    empty_test_file.write_text("# No tests in this file\nx = 1\n", encoding="utf-8")

    # Run sync runner targeting the empty test file
    res = run_targeted_tests_sync(
        test_target=str(empty_test_file),
        cwd=tmp_path,
        timeout=15,
    )

    # Pytest exits with 5 when no tests are collected.
    # Our hardened runner must treat code 5 as soft-pass with warning details.
    assert res.passed is True
    assert res.exit_code == 5
    assert res.error_type == "no_tests_collected"
    assert "no tests collected" in res.summary.lower()
    assert "collected 0 items" in (res.details or "")


# ============================================================================
# 5. Deliverable Verification Gate Tests
# ============================================================================

def test_verify_deliverables(tmp_path: Path):
    file1 = tmp_path / "artifact1.md"
    file1.write_text("# Report\nAll good.", encoding="utf-8")

    res_ok = verify_deliverables([str(file1)], cwd=tmp_path)
    assert res_ok.passed is True
    assert res_ok.check_type == "deliverables"

    missing_file = tmp_path / "missing.txt"
    res_missing = verify_deliverables([str(missing_file)], cwd=tmp_path)
    assert res_missing.passed is False
    assert res_missing.error_type == "missing_deliverables"
    assert "missing.txt" in (res_missing.details or "")

    empty_file = tmp_path / "empty.txt"
    empty_file.write_bytes(b"")
    res_empty = verify_deliverables([str(empty_file)], cwd=tmp_path)
    assert res_empty.passed is False
    assert res_empty.error_type == "empty_deliverables"
    assert "empty.txt" in (res_empty.details or "")


def test_find_missing_deliverables_helper(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing.py").write_text("x = 1", encoding="utf-8")

    content = "I have created at 'existing.py' and saved as 'missing_output.json'."
    missing = _find_missing_deliverables(content)
    assert "existing.py" not in missing
    assert "missing_output.json" in missing


# ============================================================================
# 6. Oscillation Hashing & Feedback Prompt Generation
# ============================================================================

def test_error_signature_and_oscillation_prompt():
    err1 = "SyntaxError: invalid syntax in foo.py:42"
    err2 = "SyntaxError: invalid syntax in foo.py:88"

    # Line numbers normalized to ':LINE'
    sig1 = _compute_error_signature(err1)
    sig2 = _compute_error_signature(err2)
    assert sig1 == sig2

    res = VerificationResult(
        passed=False,
        check_type="tier1_syntax",
        error_type="syntax_error",
        error_message="SyntaxError in foo.py",
        details="foo.py line 42: broken syntax",
        oscillation_count=2,
    )

    prompt = res.to_feedback_prompt(oscillation_count=2)
    assert "Repeated Failure Detected (2 consecutive identical failures)" in prompt
    assert "Do NOT re-apply the exact same change" in prompt
    assert "[Autonomous Verification Failure: tier1_syntax]" in prompt

    # With oscillation_count=0 or 1, no oscillation warning
    prompt_normal = res.to_feedback_prompt(oscillation_count=0)
    assert "You are oscillating" not in prompt_normal


# ============================================================================
# 7. Direct Loop Self-Correction & Oscillation Integration Tests
# ============================================================================

class MockGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.provider = "mock"

    async def chat(self, model=None, prompt=None, system=None, tools=None):
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        return "Finished."


class MockRegistry:
    def get_schemas(self, interactive=True, profile=None):
        return []


class MockExecutor:
    async def dispatch_tool_calls(self, tool_calls, trace_id, dry_run=False):
        return []


def test_direct_loop_self_correction_with_oscillation_feedback():
    async def _run():
        gateway = MockGateway([
            "I have written the implementation.",
            "I tried fixing it again.",
            "I found the root cause and properly fixed it now.",
        ])

        attempt = 0

        def failing_then_passing_verifier():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                return VerificationResult(
                    passed=False,
                    check_type="test_suite",
                    error_message="AssertionError: expected 42 but got 0",
                    details="test_calc.py:10: AssertionError",
                )
            elif attempt == 2:
                # Same error again to trigger oscillation detection
                return VerificationResult(
                    passed=False,
                    check_type="test_suite",
                    error_message="AssertionError: expected 42 but got 0",
                    details="test_calc.py:10: AssertionError",
                )
            else:
                return VerificationResult(
                    passed=True,
                    check_type="test_suite",
                    summary="All tests passed",
                )

        history = []
        res = await run_direct_loop(
            objective="Fix calculation bug",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            verification_fn=failing_then_passing_verifier,
            session_history=history,
            max_turns=5,
            max_verification_retries=3,
        )

        assert res is not None
        assert res.get("status") == "completed"
        assert attempt == 3

        # Verify that oscillation warning was injected into the conversation history
        history_contents = [m.get("content", "") for m in history]
        has_oscillation_warning = any("Repeated Failure Detected" in c for c in history_contents)
        assert has_oscillation_warning is True

    asyncio.run(_run())


def test_direct_loop_verification_exhaustion_with_custom_config():
    async def _run():
        gateway = MockGateway([
            "Done.",
            "Done again.",
            "Done yet again.",
            "Done 4th time.",
        ])

        v_cfg = VerificationConfig(max_verification_retries=2)
        settings = SwarmSettings(verification=v_cfg)

        attempt = 0
        def always_failing_verifier():
            nonlocal attempt
            attempt += 1
            return VerificationResult(
                passed=False,
                check_type="syntax",
                error_message="Fatal syntax error",
            )

        res = await run_direct_loop(
            objective="Do task",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            verification_fn=always_failing_verifier,
            settings=settings,
            max_turns=6,
        )

        assert res is not None
        assert res.get("status") == "incomplete"
        assert res.get("reason") == "verification_failed"
        assert attempt == 2  # respected VerificationConfig.max_verification_retries=2

    asyncio.run(_run())
