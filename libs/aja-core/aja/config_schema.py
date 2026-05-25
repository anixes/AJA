from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

class TerritoryConfig(BaseModel):
    path: str
    health_cmd: str
    auto_heal: bool = False

class ExecutionPolicy(BaseModel):
    max_timeout: float = Field(default=300.0, description="Maximum execution timeout in seconds")
    max_memory: str = Field(default="1024m", description="Maximum memory ceiling (e.g. '1024m', '2g')")
    max_cpus: float = Field(default=2.0, description="Maximum CPU ceiling (e.g. 2.0 for 2 cores)")
    allow_network_default: bool = Field(default=False, description="Default network access constraint")
    force_docker: bool = Field(default=False, description="Whether to require Docker execution to enforce hard constraints")

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
    direct_execution: bool = True

    @field_validator("operating_mode")
    @classmethod
    def validate_operating_mode(cls, v: str) -> str:
        allowed = {"offline", "online", "hybrid"}
        if v.lower() not in allowed:
            raise ValueError(f"operating_mode must be one of {allowed}, got '{v}'")
        return v.lower()

class AgentXConfig(BaseModel):
    project_name: str = "AJA"
    territories: List[TerritoryConfig] = Field(default_factory=list)
    swarm_settings: SwarmSettings = Field(default_factory=SwarmSettings)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
