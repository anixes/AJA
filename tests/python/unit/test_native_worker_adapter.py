import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aja.orchestration.adapters import dispatch_worker, NativeWorkerAdapter

@pytest.mark.anyio
async def test_native_worker_adapter_routing():
    baton = {
        "id": "1",
        "task": "Build a custom TUI panel",
        "objective": "Build a custom TUI panel",
        "run_id": "test-run",
    }
    workspace = "."

    # Patch SwarmEngine to mock execute_direct
    with patch("aja.orchestration.swarm.SwarmEngine") as MockSwarmEngine:
        mock_engine = MagicMock()
        mock_engine.execute_direct = AsyncMock()
        MockSwarmEngine.return_value = mock_engine

        # Patch Git and test utility methods of BaseAdapter
        with patch.object(NativeWorkerAdapter, "_create_branch") as mock_branch, \
             patch.object(NativeWorkerAdapter, "_get_diff", return_value="mock_diff") as mock_diff, \
             patch.object(NativeWorkerAdapter, "_run_tests", return_value="mock_tests") as mock_tests:

            result = await dispatch_worker("worker-1", baton, workspace)

            # Assertions
            mock_branch.assert_called_once_with("native-worker-1", workspace)
            mock_engine.execute_direct.assert_called_once_with("Build a custom TUI panel")
            assert result["status"] == "completed"
            assert result["diff"] == "mock_diff"
            assert result["tests"] == "mock_tests"
            assert "rollback_path" in result
