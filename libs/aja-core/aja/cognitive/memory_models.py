"""
AJA Cognitive Architecture: CoALA Memory Models
Domain models for Working Memory, Episodic Memory, Semantic Memory, and Procedural Memory.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class WorkingMemory:
    """Working Memory: Active task scratchpad holding immediate context and intermediate reasoning."""
    task_id: str
    goal: str
    active_subgoal: Optional[str] = None
    scratchpad: List[str] = field(default_factory=list)
    observations: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add_thought(self, thought: str) -> None:
        self.scratchpad.append(thought)

    def add_observation(self, tool_name: str, result: Any, status: str = "success") -> None:
        self.observations.append({
            "tool": tool_name,
            "result": result,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_recent_context(self, max_observations: int = 5) -> str:
        recent = self.observations[-max_observations:]
        lines = [f"Goal: {self.goal}"]
        if self.active_subgoal:
            lines.append(f"Current Subgoal: {self.active_subgoal}")
        if self.scratchpad:
            lines.append("Thoughts:\n" + "\n".join(f"- {t}" for t in self.scratchpad[-3:]))
        if recent:
            lines.append("Recent Observations:")
            for obs in recent:
                lines.append(f"  * [{obs['status'].upper()}] {obs['tool']}: {str(obs['result'])[:200]}")
        return "\n".join(lines)


@dataclass
class TrajectoryStep:
    """A single action-observation step within an episodic trajectory."""
    step_index: int
    action_type: str  # e.g., 'codeact', 'tool_call', 'subgoal_switch'
    action_payload: Any
    observation: Any
    duration_ms: float = 0.0
    status: str = "success"


@dataclass
class EpisodeReflection:
    """Self-reflection critique generated post-execution."""
    success: bool
    critique: str
    lessons_learned: List[str] = field(default_factory=list)
    suggested_improvements: List[str] = field(default_factory=list)


@dataclass
class TaskTrajectory:
    """Episodic Memory Entity: Full execution trajectory of a past mission with reflections."""
    episode_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ""
    domain: str = "general"  # e.g., 'sysadmin', 'web_research', 'coding'
    steps: List[TrajectoryStep] = field(default_factory=list)
    reflection: Optional[EpisodeReflection] = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None

    def mark_completed(self, success: bool, critique: str, lessons: Optional[List[str]] = None) -> None:
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.reflection = EpisodeReflection(
            success=success,
            critique=critique,
            lessons_learned=lessons or [],
        )


@dataclass
class SemanticFact:
    """Semantic Memory Entity: Factual knowledge about the environment, user, or domain."""
    category: str  # e.g., 'system_spec', 'user_preference', 'service_config'
    key: str
    value: Any
    source: str = "auto_discovery"
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ProceduralSkill:
    """Procedural Memory Entity: Reusable skill or runbook stored in agentskills.io format."""
    name: str
    description: str
    instructions: str
    script_code: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
