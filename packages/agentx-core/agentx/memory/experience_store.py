import time
import json
from typing import Dict, List, Any, Optional

class Experience:
    def __init__(self, goal: str, plan: Any, result: Any, metrics: Any, timestamp: float):
        self.goal = goal
        self.plan = plan
        self.result = result
        self.metrics = metrics
        self.timestamp = timestamp

class ExperienceStore:
    def __init__(self):
        self.store = []
        self.learning_enabled = True
        try:
            from agentx.embeddings.service import EmbeddingService
            self.embedding_service = EmbeddingService()
        except ImportError:
            self.embedding_service = None

    def save(self, goal: str, plan: Any, result: Any, metrics: Any):
        if not self.learning_enabled:
            return
            
        exp = Experience(goal, plan, result, metrics, time.time())
        success = getattr(result, "success", False) if not isinstance(result, dict) else result.get("success", False)
        error = getattr(result, "error", "") if not isinstance(result, dict) else result.get("error", "")
        latency = getattr(metrics, "latency", 0.0) if not isinstance(metrics, dict) else metrics.get("latency", 0.0)
        
        record = {
            "goal": goal,
            "goal_embedding": self.embedding_service.embed(goal) if self.embedding_service else None,
            "plan_structure": plan.to_dict() if hasattr(plan, 'to_dict') else str(plan),
            "success": success,
            "latency": latency,
            "fail_reason": error,
            "timestamp": exp.timestamp
        }
        self.store.append(record)
        
        # Stability Guard
        self._check_stability()

    def _check_stability(self):
        # If learning degrades performance:
        if len(self.store) < 10:
            return
        recent = self.store[-10:]
        success_rate = sum(1 for r in recent if r["success"]) / len(recent)
        if success_rate < 0.4:  # success_rate_drop
            print("[ExperienceStore] [STABILITY GUARD] Success rate dropped significantly. Disabling learning.")
            self.learning_enabled = False

    def retrieve_similar(self, goal: str, top_k: int = 3) -> List[Dict]:
        if not self.learning_enabled or not self.embedding_service or not self.store:
            return []
        goal_emb = self.embedding_service.embed(goal)
        from agentx.embeddings.similarity import cosine_similarity
        
        scored = []
        for record in self.store:
            if record["goal_embedding"]:
                sim = cosine_similarity(goal_emb, record["goal_embedding"])
                scored.append((sim, record))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for s, r in scored[:top_k]]

experience_store = ExperienceStore()
