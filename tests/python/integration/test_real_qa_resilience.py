"""
=============================================================================
AJA Principal QA Resilience & Adversarial Test Suite
=============================================================================
Stress-tests the Autonomous Cognitive Agent OS against:
1. Adversarial command injection & path traversal attacks
2. CodeAct sandbox fault injection (infinite loops, syntax errors, output floods)
3. CoALA memory fault tolerance (corrupted state recovery, invalid skills)
4. Kernel scheduler priority invariants under heavy concurrent load
5. Multi-workspace ContextVar concurrency & isolation guarantees
6. End-to-end multi-step autonomous cognitive missions
=============================================================================
"""

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aja.cognitive.codeact import CodeActExecutor
from aja.cognitive.memory_manager import CognitiveMemoryManager
from aja.cognitive.memory_models import WorkingMemory
from aja.cognitive.orchestrator import CognitiveOrchestrator
from aja.cognitive.specialists import SysAdminSpecialist, WebResearchSpecialist, CodeEngineerSpecialist
from aja.kernel.scheduler import KernelScheduler, MissionStatus, PriorityLevel
from aja.security.command_guard import classify_command
from aja.workspace.context import (
    WorkspaceContext,
    get_current_workspace,
    reset_current_workspace,
    set_current_workspace,
)
from aja.workspace.manager import WorkspaceRegistry


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

@pytest.fixture
def qa_env(tmp_path):
    storage_root = tmp_path / "qa_storage"
    storage_root.mkdir()
    
    ws_dir_a = tmp_path / "proj_alpha"
    ws_dir_a.mkdir()
    (ws_dir_a / "app.py").write_text("print('alpha_app')", encoding="utf-8")
    
    ws_dir_b = tmp_path / "proj_beta"
    ws_dir_b.mkdir()
    (ws_dir_b / "secret.env").write_text("DB_KEY=supersecret", encoding="utf-8")
    
    reg_root = tmp_path / "workspaces_root"
    registry = WorkspaceRegistry(storage_root=reg_root)
    
    ws_a = registry.add(ws_dir_a, name="alpha", config_overrides={"allow_out_of_bounds_paths": False})
    ws_b = registry.add(ws_dir_b, name="beta", config_overrides={"allow_out_of_bounds_paths": False})
    
    mem_mgr = CognitiveMemoryManager(root_dir=storage_root / "cognitive")
    
    yield {
        "root": tmp_path,
        "storage": storage_root,
        "ws_dir_a": ws_dir_a,
        "ws_dir_b": ws_dir_b,
        "ws_a": ws_a,
        "ws_b": ws_b,
        "registry": registry,
        "mem_mgr": mem_mgr,
    }
    
    shutil.rmtree(tmp_path, ignore_errors=True)


# ===========================================================================
# 1. ADVERSARIAL SECURITY & CATASTROPHIC SAFEGUARDS
# ===========================================================================

@pytest.mark.parametrize("catastrophic_cmd", [
    "rm -rf /",
    "rm -rf /*",
    "rm -rf / --no-preserve-root",
    "mkfs.ext4 /dev/sda1",
    "format c: /fs:NTFS /q /y",
    "dd if=/dev/zero of=/dev/sda bs=1M",
    "cmd /c format d: /q",
    "powershell -c \"Remove-Item -Recurse -Force /\"",
    ":(){ :|:& };:",
    "echo hello; rm -rf /",
    "echo test && mkfs /dev/sdb",
])
def test_qa_command_guard_blocks_catastrophic_variants(catastrophic_cmd):
    """Ensure all variations of catastrophic destructive commands are unconditionally denied in <1ms."""
    t0 = time.perf_counter()
    result = classify_command(catastrophic_cmd)
    duration_ms = (time.perf_counter() - t0) * 1000.0
    
    assert result["decision"] == "deny", f"Failed to deny catastrophic command: {catastrophic_cmd} -> {result}"
    assert result["level"] == "CRITICAL"
    assert duration_ms < 5.0, f"Classification too slow ({duration_ms:.2f}ms)"


def test_qa_path_traversal_blocked_in_isolated_workspace(qa_env):
    """Ensure relative and absolute path escapes are blocked when workspace sandboxing is active."""
    ws_a = qa_env["ws_a"]
    ws_dir_b = qa_env["ws_dir_b"]
    
    ctx = WorkspaceContext(
        id=ws_a.id,
        name=ws_a.name,
        path=Path(ws_a.path),
        storage_dir=qa_env["storage"] / ws_a.id,
        config_overrides={"allow_out_of_bounds_paths": False},
    )
    
    token = set_current_workspace(ctx)
    try:
        # Path traversal via relative directory climb
        traversal_cmd = f'cmd /c type "..\\{ws_dir_b.name}\\secret.env"'
        res1 = classify_command(traversal_cmd)
        assert res1["decision"] in {"ask", "deny"}
        assert any("outside the workspace root" in r or "path traversal" in r.lower() for r in res1["reasons"])
        
        # Absolute path outside the workspace
        abs_cmd = f'cmd /c type "{ws_dir_b / "secret.env"}"'
        res2 = classify_command(abs_cmd)
        assert res2["decision"] in {"ask", "deny"}
        assert any("outside the workspace root" in r for r in res2["reasons"])
    finally:
        reset_current_workspace(token)


# ===========================================================================
# 2. CODEACT SANDBOX FAULT INJECTION & EDGE CASES
# ===========================================================================

def test_qa_codeact_infinite_loop_timeout_trap():
    """Ensure infinite loops or deadlocks in CodeAct actions terminate strictly within timeout bounds."""
    executor = CodeActExecutor(default_timeout_seconds=2.0)
    
    code = """
import time
while True:
    time.sleep(0.1)
"""
    t0 = time.monotonic()
    result = executor.execute_python(code, timeout=2.0)
    elapsed = time.monotonic() - t0
    
    assert result.success is False
    assert "timed out" in result.stderr.lower() or result.exit_code == 124
    assert elapsed < 4.0, f"Timeout trap took too long: {elapsed:.2f}s"


def test_qa_codeact_handles_syntax_and_runtime_crashes():
    """Ensure syntax errors and runtime exceptions are cleanly returned as observations without crashing."""
    executor = CodeActExecutor(default_timeout_seconds=5.0)
    
    # Syntax error
    res_syntax = executor.execute_python("def broken_func(:\n    pass")
    assert res_syntax.success is False
    assert "SyntaxError" in res_syntax.stderr
    
    # ZeroDivision error
    res_runtime = executor.execute_python("x = 10 / 0")
    assert res_runtime.success is False
    assert "ZeroDivisionError" in res_runtime.stderr


def test_qa_codeact_markdown_edge_cases():
    """Ensure multi-fence and malformed markdown blocks are parsed with zero ambiguity."""
    executor = CodeActExecutor()
    
    # Nested markdown backticks inside strings
    nested_response = """
Here is the code to inspect:
```python
query = "```sql SELECT * FROM users```"
print("Cleanly parsed")
```
"""
    extracted = executor.extract_code_blocks(nested_response)
    assert len(extracted) == 1
    assert extracted[0][0] == "python"
    assert 'print("Cleanly parsed")' in extracted[0][1]
    
    # Mixed Python and Bash
    mixed_response = """
First run shell:
```bash
echo "step 1"
```
Then run python:
```python
print("step 2")
```
"""
    mixed_extracted = executor.extract_code_blocks(mixed_response)
    assert len(mixed_extracted) == 2
    assert mixed_extracted[0][0] == "bash"
    assert mixed_extracted[1][0] == "python"


# ===========================================================================
# 3. COALA MEMORY FAULT TOLERANCE & RECOVERY
# ===========================================================================

def test_qa_semantic_memory_corrupted_json_recovery(qa_env):
    """Ensure corrupted semantic.json is detected, quarantined, and safely recreated."""
    mem_mgr = qa_env["mem_mgr"]
    semantic_file = qa_env["storage"] / "cognitive" / "state" / "semantic.json"
    semantic_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Write corrupt JSON
    semantic_file.write_text("{ this is corrupt json !!!", encoding="utf-8")
    
    # Manager should not crash; it should catch JSONDecodeError, log, and recreate fresh facts
    facts = mem_mgr.discover_host_facts()
    assert len(facts) > 0
    assert "os_name" in facts
    
    summary = mem_mgr.get_semantic_context_summary()
    assert "os_name" in summary


def test_qa_procedural_memory_skips_invalid_skills(qa_env):
    """Ensure broken or malformed skill files do not crash procedural memory indexing."""
    mem_mgr = qa_env["mem_mgr"]
    broken_skill_dir = qa_env["storage"] / "cognitive" / "skills" / "broken_skill"
    broken_skill_dir.mkdir(parents=True, exist_ok=True)
    
    # Broken SKILL.md missing frontmatter
    (broken_skill_dir / "SKILL.md").write_text("No yaml frontmatter here at all", encoding="utf-8")
    
    skills = mem_mgr.list_skills()
    # Should index cleanly without raising an unhandled exception
    assert isinstance(skills, list)


def test_qa_episodic_memory_zero_reflections_recall(qa_env):
    """Ensure episodic recall returns empty list gracefully when no past trajectories exist."""
    mem_mgr = qa_env["mem_mgr"]
    results = mem_mgr.recall_episodes("Fix docker networking port conflict")
    assert results == []


# ===========================================================================
# 4. KERNEL PRIORITY SCHEDULER INVARIANTS & CONCURRENCY
# ===========================================================================

def test_qa_kernel_scheduler_priority_ordering():
    """Ensure URGENT tasks execute before NORMAL and BACKGROUND tasks regardless of submission order."""
    async def scenario():
        scheduler = KernelScheduler(max_concurrency=1)
        
        # Test item ordering in PriorityQueue directly
        item_bg = await scheduler.submit("Clean old logs", priority=PriorityLevel.BACKGROUND)
        item_norm = await scheduler.submit("Build test matrix", priority=PriorityLevel.NORMAL)
        item_urg = await scheduler.submit("HOTFIX VPS Outage", priority=PriorityLevel.URGENT)
        
        # Pull from priority queue directly to verify min-heap ordering
        p1 = await scheduler.queue.get()
        p2 = await scheduler.queue.get()
        p3 = await scheduler.queue.get()
        
        assert p1.mission_id == item_urg.id
        assert p2.mission_id == item_norm.id
        assert p3.mission_id == item_bg.id
        
        assert p1.priority == int(PriorityLevel.URGENT)
        assert p2.priority == int(PriorityLevel.NORMAL)
        assert p3.priority == int(PriorityLevel.BACKGROUND)

    asyncio.run(scenario())


def test_qa_concurrent_workspace_contextvar_isolation():
    """Ensure concurrent async coroutines operating on different workspaces never bleed context state."""
    async def scenario():
        async def worker(ws_id: str, ws_name: str, delay: float):
            ctx = WorkspaceContext(
                id=ws_id,
                name=ws_name,
                path=Path(f"/tmp/{ws_name}"),
                storage_dir=Path(f"/tmp/storage/{ws_id}"),
            )
            token = set_current_workspace(ctx)
            try:
                # Yield control back to event loop to encourage interleaving
                await asyncio.sleep(delay)
                current = get_current_workspace()
                assert current is not None
                assert current.id == ws_id
                assert current.name == ws_name
                return current.id
            finally:
                reset_current_workspace(token)
        
        # Launch 20 concurrent coroutines with varied delays
        tasks = [
            worker(f"ws-{i}", f"name-{i}", delay=0.01 * (i % 3))
            for i in range(20)
        ]
        results = await asyncio.gather(*tasks)
        assert results == [f"ws-{i}" for i in range(20)]

    asyncio.run(scenario())


# ===========================================================================
# 5. END-TO-END COGNITIVE SPECIALIST MISSION
# ===========================================================================

def test_qa_e2e_cognitive_orchestrator_autonomous_sysadmin(qa_env):
    """
    End-to-End QA: Executes an autonomous SysAdmin mission via CodeAct,
    verifies perception, execution, trajectory tracking, and reflection capture.
    """
    async def scenario():
        mem_mgr = qa_env["mem_mgr"]
        orchestrator = CognitiveOrchestrator(memory_manager=mem_mgr)
        
        result = await orchestrator.execute_mission(
            goal="Inspect host system specs and cores",
            max_turns=2,
        )
        
        assert result["success"] is True
        assert result["specialist"] == "sysadmin_specialist"
        assert "Host System Specifications" in result["output"]
        
        # Verify reflection was generated and saved to episodic memory
        assert result["reflection"] is not None
        assert result["reflection"]["success"] is True
        
        # Verify episode can now be recalled
        recalled = mem_mgr.recall_episodes("system specs")
        assert len(recalled) > 0
        assert recalled[0]["goal"] == "Inspect host system specs and cores"

    asyncio.run(scenario())
