import pytest
import typer
from unittest.mock import patch, MagicMock

from aja.orchestration.plan_gate import plan_gate

@pytest.mark.anyio
async def test_plan_gate_no_plan_needed():
    # Mock LLMGateway to return needs_plan: false
    with patch("aja.orchestration.gateway.LLMGateway") as mock_gateway_class:
        mock_gateway = MagicMock()
        # gateway.chat is async
        async def mock_chat(*args, **kwargs):
            if "evaluation bot" in kwargs.get("system", ""):
                return '{"needs_plan": false}'
            return "unexpected call"
        
        mock_gateway.chat = mock_chat
        mock_gateway_class.return_value = mock_gateway
        
        result = await plan_gate("ls -l")
        assert result == "ls -l"

@pytest.mark.anyio
async def test_plan_gate_plan_needed_accepted():
    # Mock LLMGateway to return needs_plan: true, then a plan
    with patch("aja.orchestration.gateway.LLMGateway") as mock_gateway_class:
        mock_gateway = MagicMock()
        async def mock_chat(*args, **kwargs):
            if "evaluation bot" in kwargs.get("system", ""):
                return '{"needs_plan": true}'
            elif "execution plans" in kwargs.get("system", ""):
                return "1. Do A\n2. Do B"
            return ""
        
        mock_gateway.chat = mock_chat
        mock_gateway_class.return_value = mock_gateway
        
        # Mock Prompt.ask to return 'y'
        with patch("rich.prompt.Prompt.ask") as mock_ask:
            mock_ask.return_value = "y"
            
            result = await plan_gate("refactor the backend")
            assert "refactor the backend" in result
            assert "Execution Plan to follow" in result
            assert "1. Do A\n2. Do B" in result

@pytest.mark.anyio
async def test_plan_gate_plan_aborted():
    with patch("aja.orchestration.gateway.LLMGateway") as mock_gateway_class:
        mock_gateway = MagicMock()
        async def mock_chat(*args, **kwargs):
            return '{"needs_plan": true}'
        
        mock_gateway.chat = mock_chat
        mock_gateway_class.return_value = mock_gateway
        
        with patch("rich.prompt.Prompt.ask") as mock_ask:
            mock_ask.return_value = "n"
            
            with pytest.raises(typer.Exit):
                await plan_gate("refactor the backend")

@pytest.mark.anyio
async def test_plan_gate_plan_adjusted():
    with patch("aja.orchestration.gateway.LLMGateway") as mock_gateway_class:
        mock_gateway = MagicMock()
        async def mock_chat(*args, **kwargs):
            if "evaluation bot" in kwargs.get("system", ""):
                return '{"needs_plan": true}'
            elif "execution plans" in kwargs.get("system", ""):
                return "1. Do A\n2. Do B"
            return ""
        
        mock_gateway.chat = mock_chat
        mock_gateway_class.return_value = mock_gateway
        
        with patch("rich.prompt.Prompt.ask") as mock_ask:
            mock_ask.return_value = "also do C"
            
            result = await plan_gate("refactor the backend")
            assert "User Adjustments:\nalso do C" in result
