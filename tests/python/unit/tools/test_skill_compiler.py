"""
=============================================================================
AJA Cognitive Architecture: Autonomous Skill Compiler Unit Tests
=============================================================================
"""

import time
import pytest
from pathlib import Path

from aja.cognitive.memory_models import TaskTrajectory, TrajectoryStep
from aja.cognitive.skill_compiler import SkillCompiler


def test_skill_compilation_from_trajectory(tmp_path):
    """Verify that a successful multi-step trajectory is compiled into agentskills.io SKILL.md and run.py."""
    skills_dir = tmp_path / "skills"
    compiler = SkillCompiler(skills_dir=skills_dir)

    trajectory = TaskTrajectory(
        episode_id="ep-101",
        goal="Audit open network ports and inspect docker containers",
        domain="sysadmin",
        steps=[
            TrajectoryStep(
                step_index=1,
                action_type="shell",
                action_payload="netstat -tuln",
                observation="Active Internet connections",
                duration_ms=45.0,
            ),
            TrajectoryStep(
                step_index=2,
                action_type="shell",
                action_payload="docker ps -a",
                observation="CONTAINER ID IMAGE STATUS",
                duration_ms=80.0,
            ),
        ],
    )
    trajectory.mark_completed(success=True, critique="Executed without error", lessons=["Ports 80 and 443 are listening"])

    result = compiler.distill_trajectory(trajectory)
    assert result is not None
    assert result.is_valid is True
    assert result.validation_error is None
    assert result.skill_obj is not None

    # Check files created
    skill_dir = result.skill_dir
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "run.py").exists()

    # Check SKILL.md contents
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "name: auto_audit_open_network_ports_and_in" in skill_md
    assert "domain: sysadmin" in skill_md

    # Check run.py contents
    run_py = (skill_dir / "run.py").read_text(encoding="utf-8")
    assert "subprocess.run" in run_py
    assert "docker ps -a" in run_py
