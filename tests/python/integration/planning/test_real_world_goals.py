"""
tests/python/integration/test_real_world_goals.py
=================================================
Real-World Integration Tests for /goal and /goal-analyzer.

Tests:
1. SMART Goal Analysis, Grading, and Actionable Suggestions.
2. Goal Progress Tracking & Quantitative Velocity Estimation.
3. Habit Formation Staging (Ignition -> Formation -> Consolidation -> Habit -> Automated).
4. Real-world Filesystem Tool Execution (Write, Read, Edit, Subprocess Run, Verification).
5. CommandGuard Sandbox Defense against Malicious Goal Injections.
6. Goal Signal Parsing Immunity against Markdown Fences & False Positives.
7. End-to-End GoalSession Execution with Relentless Audit Loop.
8. GoalEngine Lifecycle and State Serialization with SMART metadata.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aja.goals import (
    Goal,
    GoalAnalyzer,
    GoalEngine,
    HabitResult,
    ProgressResult,
    SmartValidationResult,
)
from aja.orchestration.goal_session import GoalSession, _parse_signal
from aja.orchestration.tools.executor import ToolExecutor
from aja.orchestration.tools.native import NativeToolRegistry
from aja.security.command_guard import classify_command


# ===========================================================================
# 1. SMART Goal Analyzer Tests
# ===========================================================================

def test_goal_analyzer_smart_high_quality():
    """Verify that a well-defined objective receives an 'S' or 'A' grade with milestones."""
    objective = (
        "Create a robust Python caching module and write 10 unit tests "
        "to achieve 100% test coverage within 14 days."
    )
    result = GoalAnalyzer.validate_smart(objective)

    assert isinstance(result, SmartValidationResult)
    assert result.grade in ("S", "A")
    assert result.overall_score >= 4.0
    assert result.smart_scores["specific"] >= 4.0
    assert result.smart_scores["measurable"] >= 4.0
    assert result.smart_scores["achievable"] >= 3.5
    assert result.smart_scores["time_bound"] == 5.0
    assert len(result.milestones) >= 3
    assert any("Milestone" in m for m in result.milestones)


def test_goal_analyzer_smart_health_domain():
    """Verify SMART evaluation on health and habit objectives."""
    objective = "Lose 5 kg within 6 months through consistent exercise and nutrition"
    result = GoalAnalyzer.validate_smart(objective)

    assert result.grade in ("S", "A")
    assert result.overall_score >= 4.0
    assert result.smart_scores["measurable"] >= 4.0
    assert result.smart_scores["time_bound"] == 5.0
    assert len(result.suggestions) > 0


def test_goal_analyzer_smart_vague_rejection():
    """Verify that a vague, non-quantifiable objective receives a 'C' grade with suggestions."""
    vague_objective = "Do some stuff to improve things somehow"
    result = GoalAnalyzer.validate_smart(vague_objective)

    assert result.grade == "C"
    assert result.overall_score < 3.5
    assert result.smart_scores["measurable"] <= 2.0
    assert result.smart_scores["time_bound"] <= 2.0
    assert any("quantifiable" in s.lower() or "clarify" in s.lower() for s in result.suggestions)


def test_goal_analyzer_progress_tracking_ascending():
    """Test progress tracking for an increasing target (e.g. test count)."""
    progress = GoalAnalyzer.track_progress(
        current_value=60.0,
        target_value=100.0,
        start_value=20.0,
        elapsed_days=5.0,
        total_days=10.0,
    )

    assert isinstance(progress, ProgressResult)
    assert progress.completion_percentage == 50.0
    assert progress.time_percentage == 50.0
    assert progress.velocity == 8.0  # (60 - 20) / 5
    assert progress.status == "on_track"
    assert progress.estimated_days_remaining == 5.0


def test_goal_analyzer_progress_tracking_descending():
    """Test progress tracking for a decreasing target (e.g. latency reduction or weight loss)."""
    progress = GoalAnalyzer.track_progress(
        current_value=80.0,
        target_value=70.0,
        start_value=90.0,
        elapsed_days=4.0,
        total_days=10.0,
    )

    assert progress.completion_percentage == 50.0
    # 50% completed in 40% of the timeframe means ahead of schedule
    assert progress.status == "ahead"


def test_goal_analyzer_habit_stages():
    """Verify habit stage transitions and metrics across canonical milestones."""
    # 1. Ignition (1-7 days)
    h_ign = GoalAnalyzer.analyze_habit("daily-standup", current_streak=4, longest_streak=4, total_days=5, completed_days=4)
    assert h_ign.stage == "Ignition"
    assert h_ign.next_milestone == 8

    # 2. Formation (8-21 days)
    h_form = GoalAnalyzer.analyze_habit("pytest-tdd", current_streak=15, longest_streak=15, total_days=16, completed_days=15)
    assert h_form.stage == "Formation"
    assert h_form.next_milestone == 22

    # 3. Consolidation (22-30 days)
    h_cons = GoalAnalyzer.analyze_habit("code-review", current_streak=25, longest_streak=25, total_days=26, completed_days=25)
    assert h_cons.stage == "Consolidation"
    assert h_cons.next_milestone == 31

    # 4. Habit (31-66 days)
    h_hab = GoalAnalyzer.analyze_habit("git-commit", current_streak=40, longest_streak=40, total_days=42, completed_days=40)
    assert h_hab.stage == "Habit"
    assert h_hab.next_milestone == 67

    # 5. Automated (67+ days)
    h_auto = GoalAnalyzer.analyze_habit("security-scan", current_streak=75, longest_streak=75, total_days=80, completed_days=78)
    assert h_auto.stage == "Automated"
    assert h_auto.strength_score == 10.0


# ===========================================================================
# 2. Real-World Filesystem Tool Execution
# ===========================================================================

def test_real_world_tool_execution_in_workspace(tmp_path):
    """
    Real-world test: Verifies that NativeToolRegistry and ToolExecutor execute
    actual file creation, directory inspection, content search, and editing on disk.
    """
    from aja.config import CONFIG

    with (
        patch("aja.config.PROJECT_ROOT", tmp_path),
        patch("aja.orchestration.tools.executor.PROJECT_ROOT", tmp_path),
        patch.object(CONFIG.swarm_settings, "auto_proceed_local", True),
    ):
        registry = NativeToolRegistry()
        executor = ToolExecutor()

        test_file = tmp_path / "dataset.py"
        content = 'def get_stats():\n    return {"status": "ok", "items": 42}\n'

        # 1. Write file to disk via registry
        write_result = registry.execute(
            "write_file",
            {"path": str(test_file), "content": content},
        )
        assert test_file.exists(), f"Write failed: {write_result}"

        # 2. Read file from disk via registry
        read_result = registry.execute(
            "read_file",
            {"path": str(test_file)},
        )
        assert "def get_stats():" in read_result

        # 3. Modify file content on disk via multi_replace
        replace_result = registry.execute(
            "multi_replace",
            {
                "path": str(test_file),
                "replacements": [
                    {"target": '"items": 42', "replacement": '"items": 100'}
                ],
            },
        )
        updated = test_file.read_text()
        assert '"items": 100' in updated

        # 4. Execute shell command in workspace via executor
        shell_result = executor.execute(
            f'"{sys.executable}" -c "import dataset; print(dataset.get_stats())"',
            cwd=str(tmp_path),
        )
        assert shell_result["status"] == "success", f"Shell execution failed: {shell_result}"
        assert "{'status': 'ok', 'items': 100}" in shell_result["stdout"]


# ===========================================================================
# 3. CommandGuard Sandbox Defense in Goal Missions
# ===========================================================================

def test_real_world_goal_command_guard_sandbox_defense(tmp_path):
    """
    Real-world test: Asserts that destructive and path-traversal commands
    injected during goal execution are strictly classified as DENY or ASK.
    """
    # 1. Recursive root deletion attempt
    rm_decision = classify_command("rm -rf /")
    assert rm_decision["decision"] in ("deny", "ask")
    assert rm_decision["level"] in ("HIGH", "CRITICAL")

    # 2. Windows format command attempt
    format_decision = classify_command("format C: /fs:ntfs /q")
    assert format_decision["decision"] in ("deny", "ask")
    assert format_decision["level"] in ("HIGH", "CRITICAL")

    # 3. Path traversal attack
    traversal_decision = classify_command("cat ../../../../../etc/shadow")
    assert traversal_decision["decision"] in ("deny", "ask")

    # 4. Safe local command should be allowed
    safe_decision = classify_command(f"{sys.executable} -m pytest --version")
    assert safe_decision["decision"] == "allow"
    assert safe_decision["level"] == "LOW"


# ===========================================================================
# 4. Signal Parsing Resilience in Goal Missions
# ===========================================================================

def test_real_world_goal_signal_parsing_resilience():
    """
    Real-world test: Verifies that _parse_signal ignores markdown code fences,
    extracts failure reasons, and recognizes valid goal completion signals.
    """
    # Normal completion
    assert _parse_signal("Mission accomplished.\n<signal>GOAL_COMPLETE</signal>") == ("complete", "")

    # Normal failure with reason
    status, reason = _parse_signal("<signal>GOAL_FAILED: Missing API credentials</signal>")
    assert status == "failed"
    assert reason == "Missing API credentials"

    # Immunity inside markdown code fence (should NOT trigger completion)
    fence_msg = (
        "Here is an example of what to output when finished:\n"
        "```xml\n"
        "<signal>GOAL_COMPLETE</signal>\n"
        "```\n"
        "Now I will continue working on the task."
    )
    assert _parse_signal(fence_msg) == ("continue", "")

    # Immunity inside inline code backticks
    inline_msg = "Do not output `<signal>GOAL_COMPLETE</signal>` yet."
    assert _parse_signal(inline_msg) == ("continue", "")

    # Empty or continuing message
    assert _parse_signal("Still analyzing the data files...") == ("continue", "")


# ===========================================================================
# 5. GoalSession Autonomous Execution Loop
# ===========================================================================

def test_real_world_goal_session_autonomous_run(tmp_path):
    """
    Real-world test: Simulates GoalSession executing an autonomous multi-turn
    goal loop to completion with real tool operations in the workspace.
    """
    async def _run():
        session = GoalSession(dry_run=False, max_iterations=5, timeout_seconds=30)

        # Set up a target artifact on the real disk
        target_script = tmp_path / "calculator.py"

        # Mock DirectSession._turn to simulate model performing tool action and then auditing
        turn_count = 0

        async def fake_turn(prompt, console, interactive=True):
            nonlocal turn_count
            turn_count += 1
            if turn_count == 1:
                # Step 1: Write file to real disk
                target_script.write_text("def add(a, b):\n    return a + b\n")
                session.session.session_history.append({
                    "role": "assistant",
                    "content": f"Created calculator.py at {target_script}. Running verification next.",
                })
            elif turn_count == 2:
                # Step 2: Audit and complete
                assert target_script.exists()
                session.session.session_history.append({
                    "role": "assistant",
                    "content": "Audit passed: calculator.py exists and functions correctly.\n<signal>GOAL_COMPLETE</signal>",
                })

        session.session._turn = AsyncMock(side_effect=fake_turn)

        objective = f"Create a calculator module at {target_script} and verify it."
        await session.run(objective)

        assert turn_count == 2
        assert target_script.exists()
        assert "def add(a, b):" in target_script.read_text()

    asyncio.run(_run())


# ===========================================================================
# 6. GoalEngine Lifecycle and SMART Metadata Serialization
# ===========================================================================

def test_real_world_goal_engine_lifecycle(tmp_path):
    """
    Real-world test: Verifies Goal object creation, SMART analysis attachment,
    and round-trip serialization/deserialization.
    """
    objective = "Write unit tests for the authentication handler within 3 days"
    goal = Goal(objective=objective, priority=1)

    # 1. Run SMART analysis
    smart_res = goal.analyze_smart()
    assert smart_res.overall_score >= 3.5
    assert "smart_validation" in goal.metadata
    assert goal.metadata["smart_validation"]["overall_score"] == smart_res.overall_score

    # 2. Serialize to dictionary
    serialized = goal.to_dict()
    assert serialized["objective"] == objective
    assert serialized["priority"] == 1
    assert "smart_validation" in serialized["metadata"]

    # 3. Restore from dictionary
    restored = Goal.from_dict(serialized)
    assert restored.objective == goal.objective
    assert restored.id == goal.id
    assert restored.metadata["smart_validation"]["grade"] == smart_res.grade
