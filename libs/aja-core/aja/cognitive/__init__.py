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
]

