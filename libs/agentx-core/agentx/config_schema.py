from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

class TerritoryConfig(BaseModel):
    path: str
    health_cmd: str
    auto_heal: bool = False

class SwarmModels(BaseModel):
    planner: str = "google:gemini-2.0-flash"
    worker: str = "google:gemini-2.0-flash"
    critic: Optional[str] = None

class SwarmSettings(BaseModel):
    offline_mode: bool = True
    max_agents: int = Field(default=5, ge=1, le=100)
    check_interval: int = Field(default=30, ge=1)
    models: SwarmModels = Field(default_factory=SwarmModels)
    operating_mode: str = "offline"

    @field_validator("operating_mode")
    @classmethod
    def validate_operating_mode(cls, v: str) -> str:
        allowed = {"offline", "online", "hybrid"}
        if v.lower() not in allowed:
            raise ValueError(f"operating_mode must be one of {allowed}, got '{v}'")
        return v.lower()

class AgentXConfig(BaseModel):
    project_name: str = "AgentX"
    territories: List[TerritoryConfig] = Field(default_factory=list)
    swarm_settings: SwarmSettings = Field(default_factory=SwarmSettings)
