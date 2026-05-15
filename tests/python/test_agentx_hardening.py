from pathlib import Path

from agentx.api.bridge import analyze_shell_command
from agentx.orchestration.tools.executor import ToolExecutor
from agentx.security.command_guard import classify_command
from agentx.self_evolve.reflection import evaluate_postconditions, reflect
from agentx.utils.agentx_guard import AJAGuard


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = [
    PROJECT_ROOT / "libs" / "agentx-core",
]
HARDENED_FILES = [
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "config.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "learning" / "exploration.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "learning" / "strategy_store.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "planning" / "planner.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "runtime" / "handover.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "self_evolve" / "reflection.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "security" / "command_guard.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "orchestration" / "tools" / "executor.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "interface" / "tui.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "utils" / "agentx_guard.py",
    PROJECT_ROOT / "libs" / "agentx-core" / "agentx" / "utils" / "local_extractor.py",
]


def iter_package_sources():
    for source_root in SOURCE_ROOTS:
        for path in source_root.rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def test_no_agenticai_absolute_paths_in_agent_packages():
    offenders = []
    for path in iter_package_sources():
        text = path.read_text(encoding="utf-8")
        if "d:/AgenticAI" in text or "D:/AgenticAI" in text:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_no_exact_silent_exception_pass_blocks_in_agent_packages():
    offenders = []
    for path in HARDENED_FILES:
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines[:-1]):
            stripped = line.strip()
            next_stripped = lines[idx + 1].strip()
            if stripped.startswith("except Exception") and next_stripped == "pass":
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{idx + 1}")

    assert offenders == []


def test_aja_guard_flags_windows_destructive_commands():
    guard = object.__new__(AJAGuard)

    classification = guard.classify_command(
        r"powershell -NoProfile -Command \"Remove-Item -Recurse -Force C:\tmp\old\""
    )

    assert classification["needs_analysis"] is True
    assert classification["decision"] == "ask"
    assert classification["level"] == "HIGH"
    assert "Recursive forced PowerShell deletion requires confirmation." in classification["reasons"]


def test_aja_guard_allows_read_only_powershell_inspection():
    guard = object.__new__(AJAGuard)

    classification = guard.classify_command(
        "powershell -NoProfile -Command \"Get-Process | Select-Object -First 5\""
    )

    assert classification["needs_analysis"] is False
    assert classification["level"] == "LOW"


def test_bridge_uses_shared_command_guard_policy():
    command = r"powershell -NoProfile -Command \"Remove-Item -Force -Recurse C:\tmp\old\""

    assert analyze_shell_command(command) == classify_command(command)


def test_tool_executor_blocks_shared_denies():
    result = ToolExecutor().execute("mkfs /dev/sda")

    assert result["status"] == "error"
    assert "blocked" in result["message"].lower()


def test_reflection_deterministic_postconditions_skip_llm_on_failure():
    result = {"success": True, "exit_code": 1}

    postconditions = evaluate_postconditions(result)
    reflection = reflect("run failing command", [], result)

    assert postconditions["passed"] is False
    assert reflection["success"] is False
    assert "Deterministic postconditions failed" in reflection["what_failed"]
