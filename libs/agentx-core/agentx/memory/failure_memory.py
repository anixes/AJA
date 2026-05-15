import json
from typing import Dict, List, Any

class FailureMemory:
    def __init__(self):
        self.records = []
    
    def update(self, goal: str, failed_node: str, error: Any, system_state: Dict, plan: Any):
        record = {
            "goal": goal,
            "failed_node": failed_node,
            "error_type": self._classify(error),
            "state": system_state,
            "plan_embedding": None
        }
        try:
            from agentx.embeddings.service import EmbeddingService
            record["plan_embedding"] = EmbeddingService().embed(json.dumps(plan.to_dict() if hasattr(plan, 'to_dict') else str(plan)))
        except Exception:
            pass
        self.records.append(record)
        
    def _classify(self, error: Any) -> str:
        error_lower = str(error).lower()
        if "timeout" in error_lower: return "TIMEOUT"
        if "permission" in error_lower: return "AUTH_ERROR"
        if "not found" in error_lower: return "NOT_FOUND"
        return "UNKNOWN_ERROR"

    def cluster_failures_by_embedding(self):
        # Placeholder for DB/vector-based clustering
        clusters = {}
        for idx, rec in enumerate(self.records):
            if rec["plan_embedding"]:
                clusters.setdefault(rec["error_type"], []).append(idx)
        return clusters

    def analyze_failures(self):
        types = {}
        for r in self.records:
            types[r["error_type"]] = types.get(r["error_type"], 0) + 1
        return {
            "top_failure_types": types,
            "most_failed_modes": {},
            "common_state_issues": {}
        }
        
    def similarity_to_failed_plans(self, plan: Any) -> float:
        if not self.records:
            return 0.0
            
        try:
            from agentx.embeddings.service import EmbeddingService
            from agentx.embeddings.similarity import cosine_similarity
            plan_emb = EmbeddingService().embed(json.dumps(plan.to_dict() if hasattr(plan, 'to_dict') else str(plan)))
            sims = []
            for r in self.records:
                if r["plan_embedding"]:
                    sims.append(cosine_similarity(plan_emb, r["plan_embedding"]))
            if sims:
                return max(sims)
        except Exception:
            pass
        return 0.0

failure_memory = FailureMemory()
