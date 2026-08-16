"""
AJA Cognitive Engine: Autonomous Cognitive Agent Architecture
Synthesizing CoALA Tripartite Memory, AIOS Kernel, CodeAct, and Magentic-One.
"""

from aja.cognitive.codeact import CodeActExecutor, CodeActResult
from aja.cognitive.memory_models import (
    EpisodeReflection,
    ProceduralSkill,
    SemanticFact,
    TaskTrajectory,
    TrajectoryStep,
    WorkingMemory,
)
from aja.cognitive.memory_manager import CognitiveMemoryManager
from aja.cognitive.specialists import (
    BaseSpecialist,
    CodeEngineerSpecialist,
    SysAdminSpecialist,
    WebResearchSpecialist,
)
from aja.cognitive.orchestrator import CognitiveOrchestrator
from aja.cognitive.prompts import (
    DEFAULT_SOUL,
    build_system_prompt,
    load_project_guidelines,
    load_soul,
)

__all__ = [
    "WorkingMemory",
    "TrajectoryStep",
    "EpisodeReflection",
    "TaskTrajectory",
    "SemanticFact",
    "ProceduralSkill",
    "CognitiveMemoryManager",
    "CodeActExecutor",
    "CodeActResult",
    "BaseSpecialist",
    "SysAdminSpecialist",
    "WebResearchSpecialist",
    "CodeEngineerSpecialist",
    "CognitiveOrchestrator",
    "DEFAULT_SOUL",
    "build_system_prompt",
    "load_soul",
    "load_project_guidelines",
]

