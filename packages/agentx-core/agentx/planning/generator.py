"""
agentx/planning/generator.py
=============================
Phase 14 - Candidate Generation & Refinement.

Responsible for generating multiple candidate plans for a given goal.
It leverages both retrieved methods (if available) and direct LLM generation
to produce K candidates. Also includes diversity filtering and revision logic.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from agentx.planning.models import PlanGraph
from agentx.planning.method_retriever import retrieve_methods, method_fit
from agentx.planning.scorer import estimate_complexity, COMPLEXITY_LOW, COMPLEXITY_MEDIUM
from agentx.planning.verifier import verify_plan
from agentx.embeddings.service import EmbeddingService
from agentx.embeddings.similarity import cosine_similarity


def generate_candidate_plans(goal: str, state: Dict, k: int, mode: str = "default", config: Optional[Dict] = None, history: Optional[List[str]] = None) -> List[PlanGraph]:
    """
    Generate up to K candidate plans for the goal.
    Sources:
    1. The method library (top retrieved methods).
    2. Direct LLM generation (with temperature variations if needed).
    """
    candidates: List[PlanGraph] = []

    # 1. Try retrieving methods first
    method_candidates = retrieve_methods(goal, top_n=k)
    for m, sim in method_candidates:
        template = m.get("plan_template")
        if template and template.get("nodes"):
            try:
                graph = PlanGraph.from_dict(template)
                graph.goal = goal
                # Tag it for downstream scoring/learning
                object.__setattr__(graph, "_source_method_id", m["id"]) if hasattr(graph, "__dataclass_fields__") else None
                try:
                    graph._source_method_id = m["id"]  # type: ignore[attr-defined]
                    graph._method_success_rate = m.get("metrics", {}).get("success_rate", 0.5)
                except AttributeError:
                    pass
                candidates.append(graph)
            except Exception as e:
                print(f"[Generator] Failed to instantiate method {m['id']}: {e}")

    # 2. If we need more candidates, generate via LLM
    from agentx.planning.planner import Planner
    temp_planner = Planner()
    
    attempts = 0
    while len(candidates) < k and attempts < k * 3:
        # Increase temperature slightly for subsequent attempts to ensure diversity
        temp = 0.2 + (attempts * 0.15)
        try:
            raw = temp_planner._call_llm(goal, retrieved_context=state.get("retrieved_context", ""), mode=mode, config=config, history=history)
            if raw:
                new_plan = temp_planner._parse_response(raw, goal)
                
                # Part G — Diversity Enforcement
                # Check diversity before adding
                potential_list = candidates + [new_plan]
                diverse_list = filter_diverse(potential_list)
                
                if len(diverse_list) > len(candidates):
                    candidates.append(new_plan)
                else:
                    print(f"[Generator] Candidate {attempts+1} too similar to existing. Forcing regeneration.")
                    
        except Exception as e:
            print(f"[Generator] LLM generation failed: {e}")
        attempts += 1

    return candidates[:k]


def filter_diverse(plans: List[PlanGraph], sim_threshold: float = 0.95) -> List[PlanGraph]:
    """
    Remove near-duplicate plans.
    Uses structural comparison (nodes and edges) and goal embedding if applicable.
    """
    if len(plans) <= 1:
        return plans

    diverse_plans = []
    
    for plan in plans:
        is_dup = False
        plan_nodes_set = {n.id for n in plan.primitive_nodes()}
        plan_edges_set = set(plan.edges)
        
        for kept in diverse_plans:
            kept_nodes_set = {n.id for n in kept.primitive_nodes()}
            kept_edges_set = set(kept.edges)
            
            # Simple Jaccard similarity for structure
            node_intersection = len(plan_nodes_set.intersection(kept_nodes_set))
            node_union = len(plan_nodes_set.union(kept_nodes_set))
            node_sim = node_intersection / node_union if node_union > 0 else 0.0
            
            edge_intersection = len(plan_edges_set.intersection(kept_edges_set))
            edge_union = len(plan_edges_set.union(kept_edges_set))
            edge_sim = edge_intersection / edge_union if edge_union > 0 else 1.0 # If both 0 edges, it's 1.0
            
            # If structure is highly similar, consider it a duplicate
            if node_sim > sim_threshold and edge_sim > sim_threshold:
                is_dup = True
                break
                
        if not is_dup:
            diverse_plans.append(plan)
            
    return diverse_plans


def revise_plan(plan: PlanGraph, feedback: Dict[str, Any], max_iterations: int = 2) -> PlanGraph:
    """
    If the verifier finds issues, attempt to revise the plan using LLM feedback.
    Limit to max_iterations to avoid infinite loops.
    """
    from agentx.llm import completion
    
    current_plan = plan
    iteration = 0
    
    while iteration < max_iterations and not feedback.get("valid", True):
        print(f"[Generator] Revising plan (Iteration {iteration + 1}/{max_iterations})...")
        
        plan_json = json.dumps(current_plan.to_dict(), indent=2)
        feedback_json = json.dumps(feedback, indent=2)
        
        prompt = f"""
You are a Plan Refinement agent. 
The following plan has been rejected by the Verifier.

Original Plan:
```json
{plan_json}
```

Verifier Feedback:
```json
{feedback_json}
```

Your task is to fix the plan. Add missing preconditions, resolve conflicts, and ensure structural validity.
Output the FIXED plan as a strict JSON PlanGraph object.
Do not output any other text or markdown.
"""
        try:
            response = completion(prompt, system_prompt="You are a strict planning refinement agent.")
            raw_text = response.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
                
            data = json.loads(raw_text.strip())
            new_plan = PlanGraph.from_dict(data)
            new_plan.goal = current_plan.goal
            
            # Re-verify the new plan
            feedback = verify_plan(new_plan)
            current_plan = new_plan
        except Exception as e:
            print(f"[Generator] Failed to revise plan: {e}")
            break
            
        iteration += 1
        
    return current_plan
