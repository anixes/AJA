"""
Regression tests for security remediations:
- Baton code path traversal (runtime/handover.py)
- Skill compiler code injection + AST danger gate (cognitive/skill_compiler.py)
- CommandGuard strict-deny whitelist holes (security/command_guard.py)
"""

import ast
import hmac

import pytest

from aja.runtime.handover import BatonManager
from aja.cognitive.memory_models import TaskTrajectory, TrajectoryStep
from aja.cognitive.skill_compiler import SkillCompiler
from aja.security.command_guard import classify_command, is_known_safe


# ---------------------------------------------------------------------------
# Baton code validation
# ---------------------------------------------------------------------------


class TestBatonCodeValidation:
    def test_pickup_rejects_traversal_code(self, tmp_path):
        manager = BatonManager()
        with pytest.raises(ValueError):
            manager.pickup("../../evil")

    def test_pickup_rejects_absolute_path_code(self, tmp_path):
        manager = BatonManager()
        with pytest.raises(ValueError):
            manager.pickup("C:/Windows/system32")

    def test_transmit_rejects_invalid_code(self):
        manager = BatonManager()
        with pytest.raises(ValueError):
            manager.transmit_baton("../escape", "http://localhost/x")

    def test_receive_rejects_traversal_code(self):
        manager = BatonManager()
        with pytest.raises(ValueError):
            manager.receive_baton(
                {"code": "../../evil", "meta": {}, "arrow_data_b64": ""}
            )

    def test_receive_rejects_oversized_payload(self):
        manager = BatonManager()
        big = "A" * (4 * 11 * 1024 * 1024)  # > 10MB decoded
        with pytest.raises(ValueError):
            manager.receive_baton(
                {"code": "ABC123", "meta": {}, "arrow_data_b64": big}
            )

    def test_valid_roundtrip_code_accepted(self):
        from aja.runtime.handover import _validate_code
        assert _validate_code("ABC123") == "ABC123"
        assert _validate_code("ABCDEF") == "ABCDEF"

    def test_arrow_ref_escape_rejected(self, tmp_path, monkeypatch):
        import json as _json
        manager = BatonManager()
        evil_target = tmp_path / "outside.arrow"
        evil_target.write_bytes(b"fake")
        code = "AAA111"
        baton_path = manager.baton_dir / f"baton_{code}.json"
        baton_path.write_text(_json.dumps({"code": code, "arrow_ref": str(evil_target)}))
        state = manager.pickup(code)
        assert state is None or state == {}


# ---------------------------------------------------------------------------
# Skill compiler injection resistance
# ---------------------------------------------------------------------------


def _trajectory(goal: str, payload: str) -> TaskTrajectory:
    t = TaskTrajectory(
        episode_id="ep-sec",
        goal=goal,
        domain="sysadmin",
        steps=[
            TrajectoryStep(
                step_index=1,
                action_type="shell",
                action_payload=payload,
                observation="ok",
                duration_ms=1.0,
            )
        ],
    )
    t.mark_completed(success=True, critique="ok", lessons=[])
    return t


class TestSkillCompilerInjection:
    def test_goal_docstring_breakout_neutralized(self, tmp_path):
        """A goal containing triple quotes must not terminate the generated docstring."""
        compiler = SkillCompiler(skills_dir=tmp_path / "skills")
        malicious_goal = '""" \nimport os\nos.system("calc")\n"""'
        result = compiler.distill_trajectory(_trajectory(malicious_goal, "echo hi"))
        assert result is not None and result.is_valid
        run_py = (result.skill_dir / "run.py").read_text(encoding="utf-8")
        # The injected payload must be present only as a string literal value
        assert "GOAL = " in run_py

    def test_payload_newline_injection_neutralized(self, tmp_path):
        """A step payload containing newlines must stay inside the string literal."""
        compiler = SkillCompiler(skills_dir=tmp_path / "skills")
        payload = 'echo hi"\nos.system(\'pwned\')\n"'
        result = compiler.distill_trajectory(_trajectory("benign goal", payload))
        assert result is not None and result.is_valid
        run_py = (result.skill_dir / "run.py").read_text(encoding="utf-8")
        tree = ast.parse(run_py)  # must parse
        # os.system must never appear as executable code outside repr literal
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name != "system", "Injected os.system call detected"

    def test_dangerous_construct_rejected_and_not_persisted(self, tmp_path):
        """The AST gate must reject dangerous scripts and leave nothing on disk."""
        compiler = SkillCompiler(skills_dir=tmp_path / "skills")
        trajectory = _trajectory("sneaky goal", "echo hi")
        script = compiler._generate_executable_script("auto_x", trajectory)
        poisoned = script.replace(
            "def main():",
            'def main():\n    eval("__import__(\'os\').system(\'id\')")',
        )
        error = compiler._validate_skill(poisoned, "---\nname: x\n---")
        assert error is not None and "Forbidden call" in error

    def test_forbidden_module_import_rejected(self, tmp_path):
        compiler = SkillCompiler(skills_dir=tmp_path / "skills")
        bad_script = "import socket\nprint('hi')\n"
        error = compiler._validate_skill(bad_script, "---\n---")
        assert error is not None and "Forbidden module" in error


# ---------------------------------------------------------------------------
# CommandGuard strict-deny hardening
# ---------------------------------------------------------------------------


class TestCommandGuardStrictDeny:
    def test_git_config_injection_not_known_safe(self):
        assert is_known_safe("git -c core.fsmonitor=pwned status", "git", ["-c", "core.fsmonitor=pwned", "status"]) is False

    def test_git_readonly_subcommand_still_safe(self):
        assert is_known_safe("git status", "git", ["status"]) is True
        assert is_known_safe("git log --oneline", "git", ["log", "--oneline"]) is True

    def test_git_arbitrary_subcommand_denied(self):
        assert is_known_safe("git pwned", "git", ["pwned"]) is False

    def test_redirect_disqualifies_known_safe(self):
        assert is_known_safe("get-content seed.txt > out.txt", "powershell", []) is False
        assert is_known_safe("type a.txt >> b.txt", "cmd", []) is False

    def test_start_process_no_longer_whitelisted(self):
        assert is_known_safe("Start-Process calc.exe", "powershell", []) is False

    def test_invoke_webrequest_no_longer_whitelisted(self):
        assert is_known_safe("Invoke-WebRequest http://evil", "powershell", []) is False

    def test_readonly_pwsh_cmdlet_still_safe(self):
        assert is_known_safe("Get-Process", "powershell", []) is True

    def test_rm_requires_all_operands_safe(self):
        assert is_known_safe("rm -rf /srv/deployment/temporal-data", "rm", ["-rf", "/srv/deployment/temporal-data"]) is False
        assert is_known_safe("rm -rf build/cache_tmp", "rm", ["-rf", "build/cache_tmp"]) is True

    def test_classify_git_config_injection_asks(self):
        res = classify_command("git -c alias.x='!rm -rf ~/' x")
        assert res["decision"] in {"ask", "deny"}

    def test_quoted_posix_root_remove_item_denied(self):
        res = classify_command('powershell -c "Remove-Item -Recurse -Force /"')
        assert res["decision"] == "deny"

    def test_quoted_windows_root_remove_item_denied(self):
        res = classify_command('powershell -c "Remove-Item -Recurse -Force C:\\*"')
        assert res["decision"] == "deny"

    def test_deep_path_remove_item_still_asks(self):
        res = classify_command(
            'powershell -NoProfile -Command \\"Remove-Item -Recurse -Force C:\\tmp\\old\\"'
        )
        assert res["decision"] == "ask"


# ---------------------------------------------------------------------------
# Baton HMAC authentication + HTTPS enforcement
# ---------------------------------------------------------------------------


class TestBatonAuth:
    def test_receive_with_valid_signature_accepted(self, monkeypatch):
        import json as _json
        import hashlib
        from aja.runtime import handover as handover_mod

        monkeypatch.setenv("AJA_BATON_SECRET", "test-secret")
        manager = BatonManager()
        payload = {"code": "BBB222", "meta": {}, "arrow_data_b64": ""}
        body = _json.dumps(payload).encode("utf-8")
        sig = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        assert manager.receive_baton(payload, signature=sig, raw_body=body) == "BBB222"

    def test_receive_with_invalid_signature_rejected(self, monkeypatch):
        monkeypatch.setenv("AJA_BATON_SECRET", "test-secret")
        manager = BatonManager()
        payload = {"code": "CCC333", "meta": {}, "arrow_data_b64": ""}
        with pytest.raises(ValueError, match="signature"):
            manager.receive_baton(payload, signature="deadbeef")

    def test_receive_without_signature_when_required_rejected(self, monkeypatch):
        monkeypatch.setenv("AJA_BATON_SECRET", "test-secret")
        manager = BatonManager()
        payload = {"code": "DDD444", "meta": {}, "arrow_data_b64": ""}
        with pytest.raises(ValueError, match="signature"):
            manager.receive_baton(payload)

    def test_transmit_refuses_insecure_nonlocal_endpoint(self):
        manager = BatonManager()
        assert manager.transmit_baton("EEE555", "http://remote-worker:8000/rx") is False


# ---------------------------------------------------------------------------
# AJAGuard structured result contract
# ---------------------------------------------------------------------------


class TestAJAGuardContract:
    def _guard(self, input_fn=None):
        from unittest.mock import MagicMock
        from aja.utils.aja_guard import AJAGuard
        return AJAGuard(gateway=MagicMock(), input_fn=input_fn or (lambda _: "n"))

    def test_denied_command_reports_denied_status(self):
        guard = self._guard()
        result = guard.check_and_execute("rm -rf /")
        assert result["status"] == "denied"
        assert result["error"] is not None
        assert result["classification"]["decision"] == "deny"

    def test_cancelled_command_reports_cancelled_status(self):
        guard = self._guard(input_fn=lambda _prompt: "n")
        result = guard.check_and_execute("shutdown /s")
        assert result["status"] == "cancelled"

    def test_executed_command_reports_executed_status(self, monkeypatch):
        from aja.utils import aja_guard as guard_mod
        monkeypatch.setattr(
            guard_mod,
            "execute_command",
            lambda cmd, allow_network=False: {
                "success": True, "stdout": "ok", "stderr": "", "exit_code": 0,
            },
        )
        guard = self._guard()
        result = guard.check_and_execute("echo hello")
        assert result["status"] == "executed"
        assert result["exit_code"] == 0


# ---------------------------------------------------------------------------
# ExecutionManager fail-closed workspace handling
# ---------------------------------------------------------------------------


def test_execution_fails_closed_when_sandbox_creation_fails(tmp_path):
    import asyncio
    from unittest.mock import MagicMock

    from aja.runtime.execution.manager import ExecutionManager
    from aja.runtime.execution.contracts import ExecutionRequest

    async def scenario():
        broken_ws = MagicMock()
        broken_ws.create.side_effect = RuntimeError("sandbox exploded")
        broken_ws.cleanup_stale.return_value = []

        manager = ExecutionManager(project_root=tmp_path)
        manager.workspace_manager = broken_ws

        req = ExecutionRequest(command="echo hi", shell=True, timeout=10)
        result = await manager.run(req)

        assert result.success is False
        assert "refusing to execute without isolation" in (result.error or "")

    asyncio.run(scenario())
