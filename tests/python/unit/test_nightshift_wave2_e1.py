"""Night-shift wave-2 E1: CommandGuard laundering-bypass regressions.

Covers the two CONFIRMED exploitable classes from the wave-2 security audit
(.opencode/night-shift/briefs/wave-2/G4.md):
1. Newline-chained commands collapsing into one segment (trailing binaries
   invisible to classification).
2. Quoted interpreter payloads (-c/-Command) laundering destructive content
   past the compound-operator scan.
"""

import pytest

from aja.security.command_guard import (
    classify_command,
    split_compound_command,
)


class TestNewlineChaining:
    def test_newline_is_a_segment_separator(self):
        segs = split_compound_command("echo hello\nshutdown /r")
        assert segs == ["echo hello", "shutdown /r"]

    def test_newline_chained_shutdown_is_escalated(self):
        res = classify_command("echo hello\nshutdown /r")
        assert res["decision"] in ("ask", "deny")
        assert any("shutdown" in r.lower() or "shutdown" in res.get("root", "").lower()
                   or "shutdown" in " ".join(res.get("compound_segments", []))
                   for r in res["reasons"])

    def test_newline_chained_destructive_is_not_allowed(self):
        res = classify_command("echo start\nrm -rf /\nshutdown /r")
        assert res["decision"] != "allow"

    def test_carriage_return_also_splits(self):
        segs = split_compound_command("echo a\r\nshutdown /r")
        assert "shutdown /r" in segs

    def test_newline_inside_quotes_does_not_split(self):
        segs = split_compound_command('git commit -m "line one\nline two"')
        assert len(segs) == 1

    def test_legitimate_multiline_script_fail_closed_not_allow_with_hidden_binary(self):
        """Benign multi-line scripts may ASK (fail-closed compound policy) —
        the critical contract is that trailing binaries are now VISIBLE to
        classification instead of silently allowed."""
        res = classify_command("echo line1\necho line2")
        assert res["decision"] in ("allow", "ask")
        segs = split_compound_command("echo line1\necho line2")
        assert len(segs) == 2  # both lines classified individually


class TestQuotedInterpreterLaundering:
    def test_powershell_command_laundering_is_escalated(self):
        res = classify_command('pwsh -c "Get-Process; Remove-Item -Force C:\\pagefile.sys"')
        assert res["decision"] in ("ask", "deny")
        assert res["reasons"], "expected explicit reasons, not silent escalation"

    def test_inner_destructive_segment_is_classified(self):
        res = classify_command('pwsh -c "Get-Process; Remove-Item -Force C:\\pagefile.sys"')
        segments = res.get("compound_segments", [])
        assert any("remove-item" in s.lower() for s in segments), (
            f"inner payload not expanded into segments: {segments}"
        )

    def test_powershell_command_flag_kills_fast_path(self):
        res = classify_command('powershell -Command "Get-Process"')
        assert res["decision"] in ("ask", "deny")

    def test_bash_c_laundering_is_escalated(self):
        res = classify_command('bash -c "ls; rm -rf /tmp/important"')
        assert res["decision"] in ("ask", "deny")

    def test_python_c_is_escalated(self):
        res = classify_command('python -c "import os; os.system(\'shutdown /r\')"')
        assert res["decision"] in ("ask", "deny")

    def test_encoded_command_is_escalated(self):
        res = classify_command("powershell -EncodedCommand RwBlAHQALQBQAHIAbwBjAGUAcwBzAA==")
        assert res["decision"] in ("ask", "deny")

    def test_python_pip_install_still_allowed(self):
        """The pre-existing python -m pip install allowlist must survive."""
        res = classify_command("python -m pip install requests")
        assert res["decision"] == "allow"


class TestLegitimateCommandsUnaffected:
    @pytest.mark.parametrize(
        "cmd",
        [
            "git status",
            "git log --oneline -5",
            "python --version",
            "npm install",
            "echo hello world",
            "dir",
        ],
    )
    def test_safe_commands_still_allowed(self, cmd):
        res = classify_command(cmd)
        assert res["decision"] == "allow", f"{cmd!r} -> {res['decision']}: {res['reasons']}"

    def test_powershell_readonly_still_allowed(self):
        res = classify_command("pwsh Get-Process")
        assert res["decision"] == "allow", f"read-only pwsh broke: {res['reasons']}"
