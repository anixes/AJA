"""
AJA Cognitive Architecture: CoALA Memory Manager
Manages Working Memory, Episodic Memory, Semantic Memory, and Procedural Skills.
"""

import json
import logging
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from aja.cognitive.memory_models import (
    EpisodeReflection,
    ProceduralSkill,
    SemanticFact,
    TaskTrajectory,
    TrajectoryStep,
    WorkingMemory,
)
from aja.cognitive.temporal_graph import BiTemporalEntityGraph, TemporalEntity, TemporalRelation

logger = logging.getLogger(__name__)

DEFAULT_AJA_ROOT = Path.home() / ".aja"


class CognitiveMemoryManager:
    """
    Unified CoALA Tripartite Memory Manager.
    Coordinates short-term working memory, bi-temporal entity knowledge graph,
    vector/FTS episodic memory, system semantic facts, and dynamic procedural skills.
    """

    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = (root_dir or DEFAULT_AJA_ROOT).resolve()
        self.state_dir = self.root_dir / "state"
        self.skills_dir = self.root_dir / "skills"
        self.episodes_dir = self.root_dir / "episodes"
        self.memory_dir = self.root_dir / "memory"

        self._ensure_directories()
        self._working_memories: Dict[str, WorkingMemory] = {}
        self._semantic_cache: Dict[str, SemanticFact] = {}
        self.temporal_graph = BiTemporalEntityGraph(db_path=self.state_dir / "temporal_graph.db")
        self._load_semantic_facts()

    def _ensure_directories(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # 1. WORKING MEMORY (Short-Term Scratchpad)
    # =========================================================================

    def create_working_memory(self, task_id: str, goal: str) -> WorkingMemory:
        wm = WorkingMemory(task_id=task_id, goal=goal)
        self._working_memories[task_id] = wm
        return wm

    def get_working_memory(self, task_id: str) -> Optional[WorkingMemory]:
        return self._working_memories.get(task_id)

    def clear_working_memory(self, task_id: str) -> None:
        self._working_memories.pop(task_id, None)

    # =========================================================================
    # 2. SEMANTIC MEMORY (Factual Knowledge & Host Auto-Discovery)
    # =========================================================================

    def _load_semantic_facts(self) -> None:
        facts_file = self.state_dir / "semantic.json"
        if facts_file.exists():
            try:
                data = json.loads(facts_file.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self._semantic_cache[k] = SemanticFact(
                        category=v.get("category", "general"),
                        key=k,
                        value=v.get("value"),
                        source=v.get("source", "loaded"),
                        updated_at=v.get("updated_at", ""),
                    )
            except Exception as e:
                logger.warning("Failed to load semantic memory: %s", e)

    def _save_semantic_facts(self) -> None:
        facts_file = self.state_dir / "semantic.json"
        data = {
            k: {
                "category": fact.category,
                "value": fact.value,
                "source": fact.source,
                "updated_at": fact.updated_at,
            }
            for k, fact in self._semantic_cache.items()
        }
        facts_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def record_fact(self, category: str, key: str, value: Any, source: str = "agent") -> SemanticFact:
        fact = SemanticFact(category=category, key=key, value=value, source=source)
        self._semantic_cache[f"{category}:{key}"] = fact
        self._save_semantic_facts()
        try:
            self.temporal_graph.upsert_entity(
                entity_type=category,
                name=key,
                properties={"value": value, "source": source},
            )
        except Exception as e:
            logger.debug("Failed upserting fact to temporal graph: %s", e)
        return fact

    def get_fact(self, category: str, key: str) -> Optional[Any]:
        fact = self._semantic_cache.get(f"{category}:{key}")
        return fact.value if fact else None

    def upsert_entity(self, entity_type: str, name: str, properties: Dict[str, Any], source_episode_id: Optional[str] = None) -> TemporalEntity:
        return self.temporal_graph.upsert_entity(entity_type, name, properties, source_episode_id=source_episode_id)

    def get_active_entity(self, entity_type: str, name: str) -> Optional[TemporalEntity]:
        return self.temporal_graph.get_active_entity(entity_type, name)

    def get_entity_history(self, entity_type: str, name: str) -> List[TemporalEntity]:
        return self.temporal_graph.get_entity_history(entity_type, name)

    def search_entities(self, query: str, limit: int = 10) -> List[TemporalEntity]:
        return self.temporal_graph.search_entities(query, limit=limit)

    def add_relation(self, source_id: str, target_id: str, relation_type: str, properties: Optional[Dict[str, Any]] = None) -> TemporalRelation:
        return self.temporal_graph.add_relation(source_id, target_id, relation_type, properties)

    def discover_host_facts(self) -> Dict[str, Any]:
        """Auto-probes and indexes host OS, hardware, and environment specifications."""
        facts = {
            "os_name": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "home_dir": str(Path.home()),
            "user": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
            "cores": os.cpu_count() or 1,
        }
        for k, v in facts.items():
            self.record_fact(category="system_spec", key=k, value=v, source="auto_discovery")
        return facts

    def get_semantic_context_summary(self) -> str:
        """Returns concise markdown summary of semantic environment facts for system prompts."""
        if not self._semantic_cache:
            self.discover_host_facts()

        lines = ["### Environment & System Facts:"]
        for key, fact in sorted(self._semantic_cache.items()):
            lines.append(f"- **{fact.key}**: `{fact.value}`")

        graph_summary = self.temporal_graph.get_context_summary(limit=8)
        if graph_summary:
            lines.append("")
            lines.append(graph_summary)

        return "\n".join(lines)

    # =========================================================================
    # 3. EPISODIC MEMORY (Task Trajectories & Self-Critique Reflections)
    # =========================================================================

    def save_episode(self, trajectory: TaskTrajectory) -> None:
        """Persists task trajectory to local episodic storage and vector index."""
        filename = f"episode_{trajectory.episode_id}.json"
        path = self.episodes_dir / filename
        data = {
            "episode_id": trajectory.episode_id,
            "goal": trajectory.goal,
            "domain": trajectory.domain,
            "started_at": trajectory.started_at,
            "completed_at": trajectory.completed_at,
            "steps": [
                {
                    "step_index": s.step_index,
                    "action_type": s.action_type,
                    "action_payload": str(s.action_payload),
                    "observation": str(s.observation)[:500],
                    "duration_ms": s.duration_ms,
                    "status": s.status,
                }
                for s in trajectory.steps
            ],
            "reflection": {
                "success": trajectory.reflection.success,
                "critique": trajectory.reflection.critique,
                "lessons_learned": trajectory.reflection.lessons_learned,
            } if trajectory.reflection else None,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # Vector index persistence in aja_episodes table
        try:
            from aja.embeddings.service import get_embedding_service
            from aja.memory.vector import VectorMemory

            critique = (trajectory.reflection.critique if trajectory.reflection else "") or ""
            lessons = " ".join((trajectory.reflection.lessons_learned if trajectory.reflection else []) or [])
            summary_text = f"Goal: {trajectory.goal}\nDomain: {trajectory.domain}\nCritique: {critique}\nLessons: {lessons}".strip()

            embed_svc = get_embedding_service()
            vector = embed_svc.get_embedding(summary_text)
            if vector:
                vm = VectorMemory(table_name="aja_episodes")
                vm.add(
                    text=summary_text,
                    vector=vector,
                    metadata={
                        "episode_id": trajectory.episode_id,
                        "goal": trajectory.goal,
                        "domain": trajectory.domain,
                        "success": trajectory.reflection.success if trajectory.reflection else True,
                    },
                )
        except Exception as exc:
            logger.debug("Failed adding episode %s to vector index: %s", trajectory.episode_id, exc)

    def recall_episodes(self, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Recalls relevant past episodes by semantic vector search with keyword fallback."""
        if query.strip():
            try:
                from aja.embeddings.service import get_embedding_service
                from aja.memory.vector import VectorMemory

                embed_svc = get_embedding_service()
                query_vector = embed_svc.get_embedding(query)
                if query_vector:
                    vm = VectorMemory(table_name="aja_episodes")
                    hits = vm.search(query_vector, limit=limit)
                    if hits:
                        recalled = []
                        seen_ids = set()
                        for hit in hits:
                            meta = hit.get("metadata") or {}
                            ep_id = meta.get("episode_id")
                            if ep_id and ep_id not in seen_ids:
                                seen_ids.add(ep_id)
                                ep_path = self.episodes_dir / f"episode_{ep_id}.json"
                                if ep_path.exists():
                                    try:
                                        recalled.append(json.loads(ep_path.read_text(encoding="utf-8")))
                                    except Exception:
                                        pass
                        if recalled:
                            return recalled
            except Exception as exc:
                logger.debug("Vector search for episodes failed (%s), falling back to keyword scan", exc)

        episodes = []
        q_tokens = set(query.lower().split())

        for p in self.episodes_dir.glob("episode_*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                goal_text = (data.get("goal") or "").lower()
                critique = ((data.get("reflection") or {}).get("critique") or "").lower()
                lessons = " ".join((data.get("reflection") or {}).get("lessons_learned") or []).lower()

                # Basic score calculation
                text_corpus = f"{goal_text} {critique} {lessons}"
                score = sum(1 for token in q_tokens if token in text_corpus)
                if score > 0 or not query.strip():
                    episodes.append((score, data))
            except Exception as e:
                logger.debug("Failed reading episode file %s: %s", p, e)

        episodes.sort(key=lambda x: x[0], reverse=True)
        return [ep[1] for ep in episodes[:limit]]

    # =========================================================================
    # 4. PROCEDURAL MEMORY (Dynamic Skills in agentskills.io Format)
    # =========================================================================

    def save_skill(self, skill: ProceduralSkill) -> Path:
        """Saves a skill as ~/.aja/skills/<name>/SKILL.md following agentskills.io specification."""
        skill_folder = self.skills_dir / skill.name
        skill_folder.mkdir(parents=True, exist_ok=True)

        skill_md_path = skill_folder / "SKILL.md"
        frontmatter = f"""---
name: {skill.name}
description: "{skill.description}"
tags: {json.dumps(skill.tags)}
created_at: "{skill.created_at}"
---

# {skill.name}

{skill.instructions}
"""
        skill_md_path.write_text(frontmatter, encoding="utf-8")

        if skill.script_code:
            script_path = skill_folder / "script.py"
            script_path.write_text(skill.script_code, encoding="utf-8")

        return skill_folder

    def get_skill(self, name: str) -> Optional[ProceduralSkill]:
        skill_folder = self.skills_dir / name
        skill_md = skill_folder / "SKILL.md"
        if not skill_md.exists():
            return None

        content = skill_md.read_text(encoding="utf-8")
        description = ""
        instructions = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                instructions = parts[2].strip()
                for line in parts[1].splitlines():
                    if line.startswith("description:"):
                        description = line.split("description:", 1)[1].strip().strip('"\'')

        script_code = None
        script_file = skill_folder / "script.py"
        if script_file.exists():
            script_code = script_file.read_text(encoding="utf-8")

        return ProceduralSkill(
            name=name,
            description=description,
            instructions=instructions,
            script_code=script_code,
        )

    def list_skills(self) -> List[ProceduralSkill]:
        skills = []
        if not self.skills_dir.exists():
            return skills
        for folder in self.skills_dir.iterdir():
            if folder.is_dir() and (folder / "SKILL.md").exists():
                sk = self.get_skill(folder.name)
                if sk:
                    skills.append(sk)
        return skills
