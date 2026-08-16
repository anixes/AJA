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
from aja.cognitive.skill_compiler import CompiledSkillResult, SkillCompiler
from aja.cognitive.state_tree import StateNode, StateTree
from aja.cognitive.temporal_graph import (
    BiTemporalEntityGraph,
    TemporalEntity,
    TemporalRelation,
)
from aja.cognitive.ttc_planner import CandidateBranch, TTCPlanner

__all__ = [
    "WorkingMemory",
    "TrajectoryStep",
    "EpisodeReflection",
    "TaskTrajectory",
    "SemanticFact",
    "ProceduralSkill",
    "CognitiveMemoryManager",
    "BiTemporalEntityGraph",
    "TemporalEntity",
    "TemporalRelation",
    "CodeActExecutor",
    "CodeActResult",
    "BaseSpecialist",
    "SysAdminSpecialist",
    "WebResearchSpecialist",
    "CodeEngineerSpecialist",
    "CognitiveOrchestrator",
    "StateNode",
    "StateTree",
    "CandidateBranch",
    "TTCPlanner",
    "SkillCompiler",
    "CompiledSkillResult",
    "DEFAULT_SOUL",
    "build_system_prompt",
    "load_soul",
    "load_project_guidelines",
]

