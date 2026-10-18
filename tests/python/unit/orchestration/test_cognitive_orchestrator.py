"""
Unit Tests: AJA Cognitive Orchestrator & Specialist Delegation
Validates specialist routing, prompt construction, mission execution, and reflection capture.
"""

import pytest
import shutil
from pathlib import Path

from aja.cognitive.memory_manager import CognitiveMemoryManager
from aja.cognitive.codeact import CodeActExecutor
from aja.cognitive.orchestrator import CognitiveOrchestrator
from aja.cognitive.specialists import (
    CodeEngineerSpecialist,
    SysAdminSpecialist,
    WebResearchSpecialist,
)


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.fixture
def cognitive_env(tmp_path):
    root = tmp_path / "orchestrator_test_root"
    memory_mgr = CognitiveMemoryManager(root_dir=root)
    codeact = CodeActExecutor()
    orchestrator = CognitiveOrchestrator(memory_manager=memory_mgr, codeact_executor=codeact)
    yield orchestrator, memory_mgr, root
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_specialist_routing(cognitive_env):
    orchestrator, _, _ = cognitive_env

    # Sysadmin goals
    assert isinstance(orchestrator.route_specialist("Check docker container status and disk usage"), SysAdminSpecialist)
    assert isinstance(orchestrator.route_specialist("Why is Nginx returning 502 on port 80?"), SysAdminSpecialist)

    # Web research goals
    assert isinstance(orchestrator.route_specialist("Search the web for latest Python 3.13 changelog"), WebResearchSpecialist)
    assert isinstance(orchestrator.route_specialist("Find out about FastMCP protocol documentation"), WebResearchSpecialist)

    # Coding goals
    assert isinstance(orchestrator.route_specialist("Refactor auth.py and run pytest suite"), CodeEngineerSpecialist)
    assert isinstance(orchestrator.route_specialist("Fix unit test failure in test_core.py"), CodeEngineerSpecialist)


def test_contextual_prompt_assembly(cognitive_env):
    orchestrator, memory_mgr, _ = cognitive_env

    # Record semantic fact and past episode
    memory_mgr.record_fact("server", "public_ip", "192.168.1.100")
    specialist = orchestrator.route_specialist("Inspect disk space")

    prompt = orchestrator.build_contextual_prompt("Inspect disk space", specialist)
    assert "Soul of AJA" in prompt
    assert "public_ip" in prompt
    assert "Inspect disk space" in prompt


@pytest.mark.anyio
async def test_execute_mission_sysadmin(cognitive_env):
    orchestrator, memory_mgr, root = cognitive_env

    result = await orchestrator.execute_mission("Get host system specs")
    assert result["success"] is True
    assert result["specialist"] == "sysadmin_specialist"
    assert "Host System Specifications" in result["output"]
    assert "reflection" in result

    # Check episodic memory persisted
    episodes = memory_mgr.recall_episodes("Get host system specs", limit=1)
    assert len(episodes) >= 1
    assert episodes[0]["goal"] == "Get host system specs"


@pytest.mark.anyio
async def test_execute_mission_codeact_python(cognitive_env):
    orchestrator, memory_mgr, root = cognitive_env

    python_goal = "```python\nval = 15 * 4\nprint(f'COMPUTED_VAL_{val}')\n```"
    result = await orchestrator.execute_mission(python_goal, cwd=root)

    assert result["success"] is True
    assert "COMPUTED_VAL_60" in result["output"]
    assert result["reflection"]["success"] is True
