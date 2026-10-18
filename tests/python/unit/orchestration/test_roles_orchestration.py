"""
test_roles_orchestration.py - Unit tests for heterogeneous role orchestration.
==============================================================================
"""

import asyncio
import json
from aja.orchestration.roles import RoleConfig, MissionStep, HeterogeneousOrchestrator


class MockRoleGateway:
    def __init__(self, plan_json, step_responses=None):
        self.plan_json = plan_json
        self.step_responses = list(step_responses or [])
        self.recorded_calls = []
        self.provider = "mock"

    async def chat(self, model=None, prompt=None, system=None, tools=None):
        self.recorded_calls.append({"model": model, "prompt": prompt})
        # If this is the planner call:
        if any("Software Architecture Planner" in str(m.get("content", "")) for m in (prompt or [])):
            return self.plan_json
        # Otherwise worker turn
        if self.step_responses:
            return self.step_responses.pop(0)
        return "Step finished."


class MockRegistry:
    def get_schemas(self, interactive=True):
        return []


class MockExecutor:
    async def dispatch_tool_calls(self, tool_calls, trace_id, dry_run=False):
        return []


def test_plan_decomposition():
    async def _run():
        plan_data = [
            {
                "step_id": 1,
                "title": "Create data model",
                "description": "Add User schema",
                "acceptance_criteria": ["Model compiles"],
                "verification_cmd": "python -m py_compile user.py",
            },
            {
                "step_id": 2,
                "title": "Add test case",
                "description": "Write test_user.py",
                "acceptance_criteria": ["Tests pass"],
                "verification_cmd": "pytest",
            },
        ]
        gateway = MockRoleGateway(json.dumps(plan_data))
        orchestrator = HeterogeneousOrchestrator(
            config=RoleConfig(
                planner_model="google:gemini-2.5-flash",
                worker_model="llama_cpp:qwen2.5-coder-7b-instruct-q3_k_m",
            ),
            gateway=gateway,
        )

        steps = await orchestrator.plan("Build user module")
        assert len(steps) == 2
        assert steps[0].step_id == 1
        assert steps[0].title == "Create data model"
        assert steps[0].verification_cmd == "python -m py_compile user.py"
        assert steps[1].step_id == 2
        assert steps[1].title == "Add test case"

    asyncio.run(_run())


def test_plan_fallback_on_invalid_json():
    async def _run():
        gateway = MockRoleGateway("Sorry, I am not feeling like returning JSON today.")
        orchestrator = HeterogeneousOrchestrator(
            config=RoleConfig(),
            gateway=gateway,
        )

        steps = await orchestrator.plan("Simple goal")
        assert len(steps) == 1
        assert steps[0].step_id == 1
        assert steps[0].description == "Simple goal"

    asyncio.run(_run())


def test_run_heterogeneous_mission_e2e():
    async def _run():
        plan_data = [
            {
                "step_id": 1,
                "title": "Step 1",
                "description": "Task 1",
                "acceptance_criteria": ["Done 1"],
            },
            {
                "step_id": 2,
                "title": "Step 2",
                "description": "Task 2",
                "acceptance_criteria": ["Done 2"],
            },
        ]
        gateway = MockRoleGateway(
            plan_json=json.dumps(plan_data),
            step_responses=[
                "Completed step 1 successfully.",
                "Completed step 2 successfully.",
            ],
        )

        config = RoleConfig(
            planner_model="google:gemini-2.5-flash",
            worker_model="llama_cpp:qwen2.5-coder",
        )
        orchestrator = HeterogeneousOrchestrator(config=config, gateway=gateway)

        result = await orchestrator.run_mission(
            "Two-step objective",
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
        )

        assert result["status"] == "completed"
        assert result["completed_steps"] == 2
        assert result["total_steps"] == 2

        # Verify that planner received the cloud model and worker received the local model
        models_used = [call["model"] for call in gateway.recorded_calls]
        assert "google:gemini-2.5-flash" in models_used
        assert "llama_cpp:qwen2.5-coder" in models_used

    asyncio.run(_run())
