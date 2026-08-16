"""
=============================================================================
AJA Cognitive Architecture: System Prompt Synthesis Unit Tests
=============================================================================
"""

from pathlib import Path
import pytest

from aja.cognitive.prompts import (
    DEFAULT_SOUL,
    build_system_prompt,
    load_project_guidelines,
    load_soul,
)
from aja.workspace.context import WorkspaceContext, reset_current_workspace, set_current_workspace


def test_default_soul_structure():
    """Verify that default soul defines key identity, no-fluff rules, and CoALA loop."""
    assert "AJA" in DEFAULT_SOUL
    assert "No Fluff" in DEFAULT_SOUL
    assert "CoALA Loop" in DEFAULT_SOUL
    assert "CodeAct" in DEFAULT_SOUL


def test_load_soul_custom_override(tmp_path):
    """Ensure custom SOUL.md file overrides the default soul."""
    custom_soul_file = tmp_path / "SOUL.md"
    custom_soul_file.write_text("# Custom Operator Soul\nYou are a customized test agent.", encoding="utf-8")
    
    loaded = load_soul(custom_path=custom_soul_file)
    assert "# Custom Operator Soul" in loaded
    assert "customized test agent" in loaded


def test_load_project_guidelines_from_agents_md(tmp_path):
    """Ensure workspace AGENTS.md instructions are dynamically ingested."""
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("Always format git commits with semantic prefixes.", encoding="utf-8")
    
    guidelines = load_project_guidelines(workspace_path=tmp_path)
    assert guidelines is not None
    assert "Project Guidelines (AGENTS.md)" in guidelines
    assert "semantic prefixes" in guidelines


def test_build_system_prompt_tiered_assembly(tmp_path):
    """Verify multi-tier assembly of Soul + Specialist + CodeAct + Workspace + Facts + Skills + Reflections."""
    ctx = WorkspaceContext(
        id="ws-test-42",
        name="test-repo",
        path=tmp_path,
        storage_dir=tmp_path / "storage",
        config_overrides={"allow_out_of_bounds_paths": False},
    )
    token = set_current_workspace(ctx)
    try:
        past_episodes = [
            {
                "goal": "Fix docker port collision",
                "reflection": {
                    "critique": "Port 80 was occupied by nginx",
                    "lessons_learned": ["Always check active ports before binding"],
                },
            }
        ]
        
        class MockSkill:
            name = "disk_cleaner"
            description = "Cleans old build cache artifacts"

        prompt = build_system_prompt(
            goal="Diagnose and restart failing PostgreSQL cluster",
            specialist_name="SysAdminSpecialist",
            specialist_instructions="Focus on systemctl and pg_isready diagnostics.",
            past_episodes=past_episodes,
            available_skills=[MockSkill()],
        )

        # 1. Identity / Soul
        assert "Soul of AJA" in prompt
        # 2. Specialist role
        assert "Active Role: SYSADMINSPECIALIST" in prompt
        assert "pg_isready" in prompt
        # 3. CodeAct Engine guidance
        assert "Unified Action Space (CodeAct Engine)" in prompt
        # 4. Workspace Context
        assert "ws-test-42" in prompt
        assert "Isolated Workspace Sandboxed" in prompt
        # 5. Host Environment Facts
        assert "Host Environment Facts" in prompt
        # 6. Procedural Skills
        assert "disk_cleaner" in prompt
        # 7. Episodic Recall
        assert "Fix docker port collision" in prompt
        assert "Always check active ports before binding" in prompt
        # 8. Objective
        assert "Diagnose and restart failing PostgreSQL cluster" in prompt
    finally:
        reset_current_workspace(token)
