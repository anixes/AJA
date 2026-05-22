import pytest
from pydantic import ValidationError
from agentx.config_schema import AgentXConfig, SwarmSettings

def test_valid_config():
    data = {
        "project_name": "TestAgentX",
        "territories": [
            {"path": "apps/test-app", "health_cmd": "echo 1", "auto_heal": True}
        ],
        "swarm_settings": {
            "offline_mode": False,
            "max_agents": 10,
            "check_interval": 45,
            "operating_mode": "hybrid"
        }
    }
    config = AgentXConfig.model_validate(data)
    assert config.project_name == "TestAgentX"
    assert len(config.territories) == 1
    assert config.swarm_settings.operating_mode == "hybrid"
    assert config.swarm_settings.max_agents == 10

def test_invalid_operating_mode():
    data = {
        "swarm_settings": {
            "operating_mode": "invalid_mode"
        }
    }
    with pytest.raises(ValidationError) as excinfo:
        AgentXConfig.model_validate(data)
    assert "operating_mode must be one of" in str(excinfo.value)

def test_invalid_max_agents():
    data = {
        "swarm_settings": {
            "max_agents": 200  # Max is 100
        }
    }
    with pytest.raises(ValidationError) as excinfo:
        AgentXConfig.model_validate(data)
    assert "Input should be less than or equal to 100" in str(excinfo.value)

def test_invalid_max_agents_low():
    data = {
        "swarm_settings": {
            "max_agents": 0  # Min is 1
        }
    }
    with pytest.raises(ValidationError) as excinfo:
        AgentXConfig.model_validate(data)
    assert "Input should be greater than or equal to 1" in str(excinfo.value)
