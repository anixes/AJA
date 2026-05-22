import pytest
import asyncio
from unittest.mock import patch, AsyncMock
from agentx.config import CONFIG
from agentx.config_schema import SwarmSettings
from agentx.orchestration.swarm import SwarmEngine

def test_direct_execution_config_default():
    """
    Verify that the direct_execution option defaults to True in SwarmSettings.
    """
    settings = SwarmSettings()
    assert settings.direct_execution is True

def test_swarm_engine_execute_direct_dry_run():
    """
    Verify that SwarmEngine's execute_direct method runs cleanly in dry-run mode
    using a mocked LLM gateway.
    """
    async def run_test():
        # Initialize with dry_run = True
        engine = SwarmEngine(dry_run=True)
        
        # Run a simple mock command
        objective = "list the number of projects inside agentic ai folder in d drive"
        
        # We patch engine.gateway.chat to avoid hitting external API endpoints
        with patch.object(engine.gateway, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = "I have scanned the system and completed the task successfully, Sir."
            
            await engine.execute_direct(objective)
            
            # Assert that the chat gateway was indeed invoked
            mock_chat.assert_called()

    asyncio.run(run_test())
