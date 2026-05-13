"""
agentx/planning/failure_memory.py
==================================
Phase 14 - Failure Memory & Adaptation.

Maintains a persistent record of failed goals and plans.
Provides mechanisms to penalize or reject similar plans in the future.
"""
from typing import Dict, List, Any
import os
import json
from agentx.planning.models import PlanGraph
from agentx.embeddings.service import EmbeddingService
from agentx.embeddings.similarity import cosine_similarity

FAILURE_STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "failures.json")

class FailureMemory:
    """Tracks failed plans to prevent infinite loops of repeated mistakes."""
    
    _failures: List[Dict[str, Any]] = []
    _loaded: bool = False

    @classmethod
    def _load(cls):
        if cls._loaded: return
        cls._loaded = True
        try:
            if os.path.exists(FAILURE_STORE_PATH):
                with open(FAILURE_STORE_PATH, "r", encoding="utf-8") as f:
                    cls._failures = json.load(f)
        except Exception as e:
            print(f"[FailureMemory] Warning: could not load failures: {e}")
            cls._failures = []

    @classmethod
    def _save(cls):
        try:
            os.makedirs(os.path.dirname(FAILURE_STORE_PATH), exist_ok=True)
            with open(FAILURE_STORE_PATH, "w", encoding="utf-8") as f:
                json.dump(cls._failures, f, indent=2)
        except Exception as e:
            print(f"[FailureMemory] Warning: could not save failures: {e}")

    @classmethod
    def record(cls, payload: Dict[str, Any]):
        """Records a failed plan and its embeddings to persistent memory."""
        cls._load()
        
        goal = payload.get("goal", "")
        if not goal:
            return
            
        # The payload already contains plan_embedding if coming from Phase 15 code
        # But if it doesn't, we create the embedding.
        plan_embedding = payload.get("plan_embedding")
        if plan_embedding is None:
            emb_service = EmbeddingService()
            plan_embedding = emb_service.embed_text(goal)
            
        record_data = {
            "goal": goal,
            "goal_embedding": plan_embedding,
            "node": payload.get("node"),
            "state": payload.get("state"),
            "plan_node_ids": payload.get("plan_node_ids", []),
            "error": payload.get("error", "")
        }
        cls._failures.append(record_data)
        cls._save()
        print(f"[FailureMemory] Recorded failure for goal '{goal[:50]}...'")

    @classmethod
    def get_failure_penalty(cls, goal: str, plan: PlanGraph) -> float:
        """
        Calculates a penalty (0.0 to 1.0) if this plan is suspiciously similar
        to a previously failed plan for a similar goal.
        """
        cls._load()
        if not cls._failures:
            return 0.0
            
        emb_service = EmbeddingService()
        goal_emb = emb_service.embed_text(goal)
        
        max_penalty = 0.0
        plan_nodes_set = {n.id for n in plan.primitive_nodes()}
        
        for f in cls._failures:
            # 1. Goal similarity
            g_sim = cosine_similarity(goal_emb, f["goal_embedding"])
            if g_sim < 0.8:
                continue # Goals are different, ignore
                
            # 2. Plan structural similarity
            f_nodes_set = set(f["plan_node_ids"])
            node_intersection = len(plan_nodes_set.intersection(f_nodes_set))
            node_union = len(plan_nodes_set.union(f_nodes_set))
            node_sim = node_intersection / node_union if node_union > 0 else 0.0
            
            if node_sim > 0.8:
                # Highly similar plan for a highly similar goal failed before
                penalty = g_sim * node_sim
                max_penalty = max(max_penalty, penalty)
                
        return max_penalty
