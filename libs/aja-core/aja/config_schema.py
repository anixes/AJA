from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

class TerritoryConfig(BaseModel):
    path: str
    health_cmd: str
    auto_heal: bool = False

class ExecutionPolicy(BaseModel):
    max_timeout: float = Field(default=900.0, description="Maximum execution timeout in seconds (multi-turn LLM workers need headroom)")
    max_memory: str = Field(default="1024m", description="Maximum memory ceiling (e.g. '1024m', '2g')")
    max_cpus: float = Field(default=2.0, description="Maximum CPU ceiling (e.g. 2.0 for 2 cores)")
    allow_network_default: bool = Field(default=False, description="Default network access constraint")
    force_docker: bool = Field(default=False, description="Whether to require Docker execution to enforce hard constraints")

PermissionDecision = Literal["allow", "deny", "ask"]

class PermissionPolicyConfig(BaseModel):
    scopes: Dict[str, PermissionDecision] = Field(
        default_factory=lambda: {
            "shell.*": "allow",
            "shell.destructive": "ask",
            "shell.exec.dangerous": "ask",
            "python.*": "allow",
            "mcp.*": "ask",
            # Web research tools (Phase 6): read-only GETs, allowed by default
            # so autonomous research missions run unattended.
            "web.read": "allow",
            "web.search": "allow",
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
    sandbox_mode: Literal["local", "docker"] = "local"
    auto_proceed_local: bool = False
    neutral_prompts: bool = Field(
        default=False,
        description="Swap the AJA persona system prompt for a neutral operator variant (evals/benchmarks).",
    )
    embedding_backend: str = Field(
        default="auto",
        description=(
            "Embedding backend: 'auto' | 'sentence_transformers' | 'onnx' | 'mock'. "
            "'auto' prefers the ONNX runtime (fastembed, vector-compatible MiniLM "
            "weights) when installed, else sentence-transformers. Overridable via "
            "the AJA_EMBEDDING_BACKEND env var; AJA_MOCK_EMBEDDINGS=1 forces mock."
        ),
    )
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description=(
            "Embedding model: 'all-MiniLM-L6-v2' (default) or 'bge-small-en-v1.5'. "
            "Both are 384-dim. Changing the model REQUIRES reindexing vector "
            "stores: run 'aja reindex-embeddings' after switching."
        ),
    )
    context_limit_tokens: Optional[int] = Field(
        default=None,
        ge=1024,
        description=(
            "Explicit context-window size in tokens for the active worker model. "
            "When set, this overrides the built-in model-limit table in context_window.py. "
            "Useful for custom or fine-tuned models whose real context window is not "
            "in the default lookup table."
        ),
    )

    @field_validator("operating_mode")
    @classmethod
    def validate_operating_mode(cls, v: str) -> str:
        allowed = {"offline", "online", "hybrid"}
        if v.lower() not in allowed:
            raise ValueError(f"operating_mode must be one of {allowed}, got '{v}'")
        return v.lower()

class GatewayAuthConfig(BaseModel):
    """Per-platform gateway authorization allowlists.

    These mirror (and are superseded by) the environment variables
    TELEGRAM_ALLOWED_USER_ID / DISCORD_ALLOWED_USER_IDS / SLACK_ALLOWED_USER_IDS.
    Comma-separated lists are supported for discord/slack; "*" = allow all.
    Fail-safe: when a platform bot token is configured without an allowlist,
    remote users are DENIED.
    """
    telegram_allowed_user_id: Optional[str] = Field(
        default=None, description="Env: TELEGRAM_ALLOWED_USER_ID. Single authorized Telegram user id ('*' allows all)."
    )
    discord_allowed_user_ids: Optional[str] = Field(
        default=None, description="Env: DISCORD_ALLOWED_USER_IDS. Comma-separated Discord user ids allowed to command AJA."
    )
    slack_allowed_user_ids: Optional[str] = Field(
        default=None, description="Env: SLACK_ALLOWED_USER_IDS. Comma-separated Slack member ids allowed to command AJA."
    )

class GoogleCalendarSettings(BaseModel):
    """Google Calendar integration settings (see aja.calendar and
    docs/operator/CALENDAR.md). OAuth tokens live in the OS keyring under
    service "AJA" / username "gcal" with an ACL-restricted .env fallback."""

    enabled: bool = Field(
        default=False,
        description="Master switch for calendar features; off by default.",
    )
    calendar_ids: List[str] = Field(
        default_factory=lambda: ["primary"],
        description="Calendar IDs to read from ('primary' is the user's main calendar).",
    )
    sync_interval_minutes: int = Field(
        default=60,
        ge=1,
        description="How often background graph syncs pull upcoming events.",
    )


class FileWatcherRule(BaseModel):
    path: str = Field(description="Directory to watch for file changes.")
    patterns: List[str] = Field(default_factory=lambda: ["*"], description="Glob patterns to match filenames.")
    goal: str = Field(description="Objective to fire on trigger; {changed_files} placeholder is replaced with changed paths.")
    recursive: bool = Field(default=True, description="Watch subdirectories recursively.")
    debounce_seconds: float = Field(default=2.0, ge=0.1, description="Seconds to wait after last event before firing.")
    enabled: bool = Field(default=True)


class FileWatcherSettings(BaseModel):
    rules: List[FileWatcherRule] = Field(default_factory=list)


class AJAConfig(BaseModel):
    project_name: str = "AJA"
    territories: List[TerritoryConfig] = Field(default_factory=list)
    swarm_settings: SwarmSettings = Field(default_factory=SwarmSettings)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    permission_policy: PermissionPolicyConfig = Field(default_factory=PermissionPolicyConfig)
    gateway_auth: GatewayAuthConfig = Field(default_factory=GatewayAuthConfig)
    mcp_servers: List[MCPServerConfig] = Field(default_factory=list)
    google_calendar: Optional[GoogleCalendarSettings] = None
    file_watchers: Optional[FileWatcherSettings] = None
