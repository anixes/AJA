"""
AJA Cognitive Engine: Autonomous Cognitive Agent Architecture
Synthesizing CoALA Tripartite Memory, AIOS Kernel, CodeAct, and Magentic-One.
"""

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

_LAZY_MODULES = {
    "WorkingMemory": "aja.cognitive.memory_models",
    "TrajectoryStep": "aja.cognitive.memory_models",
    "EpisodeReflection": "aja.cognitive.memory_models",
    "TaskTrajectory": "aja.cognitive.memory_models",
    "SemanticFact": "aja.cognitive.memory_models",
    "ProceduralSkill": "aja.cognitive.memory_models",
    "CognitiveMemoryManager": "aja.cognitive.memory_manager",
    "BiTemporalEntityGraph": "aja.cognitive.temporal_graph",
    "TemporalEntity": "aja.cognitive.temporal_graph",
    "TemporalRelation": "aja.cognitive.temporal_graph",
    "CodeActExecutor": "aja.cognitive.codeact",
    "CodeActResult": "aja.cognitive.codeact",
    "BaseSpecialist": "aja.cognitive.specialists",
    "SysAdminSpecialist": "aja.cognitive.specialists",
    "WebResearchSpecialist": "aja.cognitive.specialists",
    "CodeEngineerSpecialist": "aja.cognitive.specialists",
    "CognitiveOrchestrator": "aja.cognitive.orchestrator",
    "StateNode": "aja.cognitive.state_tree",
    "StateTree": "aja.cognitive.state_tree",
    "CandidateBranch": "aja.cognitive.ttc_planner",
    "TTCPlanner": "aja.cognitive.ttc_planner",
    "SkillCompiler": "aja.cognitive.skill_compiler",
    "CompiledSkillResult": "aja.cognitive.skill_compiler",
}


def __getattr__(name: str):
    if name in _LAZY_MODULES:
        import importlib

        mod = importlib.import_module(_LAZY_MODULES[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
