from typing import Dict, List, Literal, Optional
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

PermissionDecision = Literal["allow", "deny", "ask"]

class PermissionPolicyConfig(BaseModel):
    scopes: Dict[str, PermissionDecision] = Field(
        default_factory=lambda: {
            "shell.*": "allow",
            "python.*": "allow",
            "mcp.*": "ask",
            "browser.read": "allow",
            "browser.navigate": "ask",
            "browser.interact": "ask",
            "desktop.interact": "ask",
        }
    )
    ask_timeout_s: float = Field(default=60.0, ge=0.0)

class MCPServerConfig(BaseModel):
    server_id: str
    transport: Literal["stdio", "sse"] = "stdio"
    enabled: bool = True
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    permission_scope: Optional[str] = None

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
    allow_out_of_bounds_paths: bool = False

    @field_validator("operating_mode")
    @classmethod
    def validate_operating_mode(cls, v: str) -> str:
        allowed = {"offline", "online", "hybrid"}
        if v.lower() not in allowed:
            raise ValueError(f"operating_mode must be one of {allowed}, got '{v}'")
        return v.lower()

class AJAConfig(BaseModel):
    project_name: str = "AJA"
    territories: List[TerritoryConfig] = Field(default_factory=list)
    swarm_settings: SwarmSettings = Field(default_factory=SwarmSettings)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    permission_policy: PermissionPolicyConfig = Field(default_factory=PermissionPolicyConfig)
    mcp_servers: List[MCPServerConfig] = Field(default_factory=list)
