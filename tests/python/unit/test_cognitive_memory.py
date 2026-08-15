"""
Unit Tests: AJA CoALA Tripartite Memory Architecture
Validates Working Memory, Semantic Memory, Episodic Memory, and Procedural Skills.
"""

import pytest
import shutil
from pathlib import Path

from aja.cognitive.memory_models import (
    EpisodeReflection,
    ProceduralSkill,
    SemanticFact,
    TaskTrajectory,
    TrajectoryStep,
    WorkingMemory,
)
from aja.cognitive.memory_manager import CognitiveMemoryManager


@pytest.fixture
def temp_memory_manager(tmp_path):
    root = tmp_path / "aja_cognitive_test"
    mgr = CognitiveMemoryManager(root_dir=root)
    yield mgr, root
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_working_memory_lifecycle():
    wm = WorkingMemory(task_id="task-123", goal="Diagnose server memory leak")
    wm.add_thought("Checking active python processes")
    wm.add_observation("get_system_specs", {"cpu": "20%", "ram_used": "4GB"}, status="success")

    assert wm.goal == "Diagnose server memory leak"
    assert len(wm.scratchpad) == 1
    assert len(wm.observations) == 1

    context = wm.get_recent_context()
    assert "Diagnose server memory leak" in context
    assert "Checking active python processes" in context
    assert "get_system_specs" in context


def test_semantic_memory_discovery_and_persistence(temp_memory_manager):
    mgr, root = temp_memory_manager

    # 1. Discover host facts
    facts = mgr.discover_host_facts()
    assert "os_name" in facts
    assert "architecture" in facts
    assert "python_version" in facts

    # 2. Record custom fact
    mgr.record_fact(category="service_config", key="nginx_port", value=8080)
    assert mgr.get_fact("service_config", "nginx_port") == 8080

    # 3. Verify persistent reload
    reloaded_mgr = CognitiveMemoryManager(root_dir=root)
    assert reloaded_mgr.get_fact("service_config", "nginx_port") == 8080

    # 4. Summary generation
    summary = mgr.get_semantic_context_summary()
    assert "os_name" in summary
    assert "nginx_port" in summary


def test_episodic_memory_trajectory_and_recall(temp_memory_manager):
    mgr, root = temp_memory_manager

    # 1. Create and save trajectory
    traj = TaskTrajectory(
        goal="Fix broken Nginx 502 gateway",
        domain="sysadmin",
    )
    traj.steps.append(TrajectoryStep(
        step_index=1,
        action_type="tool_call",
        action_payload="get_service_status('nginx')",
        observation="Service inactive (failed)",
    ))
    traj.mark_completed(
        success=True,
        critique="Restarted php-fpm upstream socket which solved Nginx 502.",
        lessons=["Always check upstream socket permissions first."],
    )
    mgr.save_episode(traj)

    # 2. Recall similar episode
    recalled = mgr.recall_episodes("nginx gateway 502 error", limit=2)
    assert len(recalled) >= 1
    top = recalled[0]
    assert "Nginx 502" in top["goal"]
    assert "php-fpm" in top["reflection"]["critique"]


def test_procedural_memory_skill_lifecycle(temp_memory_manager):
    mgr, root = temp_memory_manager

    skill = ProceduralSkill(
        name="nginx_fpm_doctor",
        description="Diagnoses and fixes Nginx to PHP-FPM unix socket disconnects",
        instructions="1. Check /var/run/php/php-fpm.sock exists\n2. Verify www-data permissions",
        script_code="import os\nprint('Checking socket permissions')",
        tags=["nginx", "sysadmin", "php"],
    )

    # 1. Save skill
    mgr.save_skill(skill)

    # 2. List skills
    skills = mgr.list_skills()
    assert len(skills) == 1
    assert skills[0].name == "nginx_fpm_doctor"

    # 3. Retrieve skill and verify content
    retrieved = mgr.get_skill("nginx_fpm_doctor")
    assert retrieved is not None
    assert "Diagnoses and fixes Nginx" in retrieved.description
    assert "Checking socket permissions" in (retrieved.script_code or "")
