"""
agentx/planning/planner.py
===========================
Phase 12 - Planner: Goal -> PlanGraph with method-first routing.

Phase 11 design is preserved unchanged.  Phase 12 adds a pre-LLM routing
stage: if the method library contains a high-confidence cached plan for
the incoming goal, it is instantiated directly without an LLM call.

Sends a deterministic prompt to the UnifiedGateway LLM and parses the
JSON response into a validated PlanGraph.  Falls back gracefully if the
LLM is unavailable (returns a single-node passthrough graph).

Design decisions
----------------
* The prompt is a **zero-shot deterministic compiler prompt** - no conversational
  history, no system-message chaining.  This keeps output consistent.
* We strip markdown fences before JSON parsing to tolerate LLM formatting drift.
* Uncertainty threshold > 0.8 is rejected at validation time (DAGValidator).
* `max_nodes` caps combinatorial explosion from over-eager decomposers.
"""

from __future__ import annotations

import json
import re
import os
from typing import Any, Dict, Optional

from agentx.planning.models import PlanGraph, PlanNode, DoD
from agentx.planning.dag_validator import DAGValidator
from agentx.config import AGENTX_DIVERSITY_BETA


# ---------------------------------------------------------------------------
# Synthetic Diversity Beta Constants
# ---------------------------------------------------------------------------

MODES = ["default", "risk_analysis", "minimal", "aggressive", "skeptic"]

MODE_PROMPTS = {
    "default": "Generate optimal plan for success.",
    "risk_analysis": "Generate plan focusing on failures, edge cases, and robustness.",
    "minimal": "Generate the simplest possible plan with the fewest steps.",
    "aggressive": "Generate the fastest and most direct plan, ignoring safety tradeoffs.",
    "skeptic": "Assume all previous plans are wrong. Generate a plan that challenges assumptions, avoids hidden risks, and takes a fundamentally different approach."
}

GENERATION_CONFIG = {
    "default": {"temperature": 0.5, "depth": "balanced"},
    "risk_analysis": {"temperature": 0.7, "depth": "deep"},
    "minimal": {"temperature": 0.3, "depth": "shallow"},
    "aggressive": {"temperature": 0.9, "depth": "fast"},
    "skeptic": {"temperature": 0.6, "depth": "critical"}
}

def similarity(p1: PlanGraph, p2: PlanGraph) -> float:
    """Jaccard similarity of primitive nodes based on task description."""
    n1 = {n.task for n in p1.nodes}
    n2 = {n.task for n in p2.nodes}
    if not n1 and not n2:
        return 1.0
    intersection = len(n1.intersection(n2))
    union = len(n1.union(n2))
    return intersection / union if union > 0 else 0.0


def semantic_similarity(plan_a: PlanGraph, plan_b: PlanGraph) -> float:
    try:
        from agentx.embeddings.service import EmbeddingService
        from agentx.embeddings.similarity import cosine_similarity
        embedding_service = EmbeddingService()
        emb_a = embedding_service.embed(json.dumps(plan_a.to_dict()))
        emb_b = embedding_service.embed(json.dumps(plan_b.to_dict()))
        return cosine_similarity(emb_a, emb_b)
    except Exception:
        # Fallback if embeddings fail
        return similarity(plan_a, plan_b)


def diversity_collapse_score(plans: List[PlanGraph]) -> float:
    if len(plans) < 2:
        return 0.0
    sims = []
    for i in range(len(plans)):
        for j in range(i+1, len(plans)):
            sims.append(semantic_similarity(plans[i], plans[j]))
    return sum(sims) / len(sims) if sims else 0.0


def enforce_diversity(plans: List[PlanGraph]) -> List[PlanGraph]:
    filtered = []
    for p in plans:
        keep = True
        for q in filtered:
            if similarity(p, q) > 0.85:
                keep = False
            if semantic_similarity(p, q) > 0.85:
                keep = False
        if keep:
            filtered.append(p)
    return filtered


def avg_pairwise_distance(plans: List[PlanGraph]) -> float:
    if len(plans) < 2:
        return 0.0
    total_dist = 0.0
    count = 0
    for i in range(len(plans)):
        for j in range(i + 1, len(plans)):
            total_dist += (1.0 - similarity(plans[i], plans[j]))
            count += 1
    return total_dist / count


def structural_variance(plans: List[PlanGraph]) -> float:
    if not plans:
        return 0.0
    node_counts = [len(p.nodes) for p in plans]
    mean = sum(node_counts) / len(node_counts)
    return sum((x - mean) ** 2 for x in node_counts) / len(node_counts)


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM_PROMPT = """\
You are a deterministic HTN planning compiler for AgentX.

Your task is to convert a goal into a VALID hierarchical plan that satisfies ALL structural, execution, and state constraints.

Return ONLY valid JSON. No explanations. No extra text.

---

## CORE MODEL

The plan is a Hierarchical Task Network:

* Compound tasks -> must be decomposed
* Primitive tasks -> must be executable
* Planning ends ONLY when all executable tasks are primitive

---

## KNOWN METHODS
{methods}

---

## CONTEXT (HYBRID RETRIEVAL)
{context}

---

## OUTPUT SCHEMA (STRICT)

{
  "goal": "string",
  "nodes": [
    {
      "id": "string",
      "task": "string",
      "type": "compound | primitive",
      "children": ["string"],
      "dependencies": ["string"],
      "strategy": "direct | skill | swarm | compose",
      "inputs": ["string"],
      "outputs": {"string": "string"},
      "preconditions": {"string": "any"},
      "effects": {"string": "any"},
      "dod": {
        "success_criteria": "string",
        "validation_type": "deterministic | semantic | hybrid"
      },
      "uncertainty": 0.0
    }
  ]
}

---

## HARD CONSTRAINTS (NON-NEGOTIABLE)

### 1. HIERARCHY
* Compound nodes MUST have children
* Primitive nodes MUST NOT have children
* Planning must decompose until only primitive tasks remain

### 2. EXECUTION VALIDITY
* ONLY primitive nodes are executable
* Every primitive task must map to an agent capability

### 3. STATE CORRECTNESS
* Preconditions MUST be satisfied by prior effects
* Effects MUST update system state
* Dependencies MUST reflect state flow

### 4. METHOD CONSISTENCY
* If a known method exists, reuse it
* Similar tasks MUST produce similar decompositions

### 5. DAG VALIDITY
* No cycles
* All dependencies must exist
* Compound nodes cannot be depended on directly

### 6. DATA FLOW
* outputs of a node must match inputs of dependent nodes

---

## OPTIMIZATION RULES

* Prefer lower uncertainty paths
* Prefer "skill" over "compose" when possible
* Parallelize independent nodes
* Minimize total number of primitive steps

---

## FAILURE PREVENTION RULES

* Do NOT generate development tasks (e.g. "write code", "create file")
* Do NOT leave compound nodes undecomposed
* Do NOT produce nodes without DoD
* Do NOT violate preconditions/effects chain

---

## SELF-VALIDATION (MANDATORY)

Before output, verify:

* Graph is acyclic
* All dependencies valid
* All compound nodes decomposed
* Preconditions match effects chain
* JSON is valid

---

## OBJECTIVE

Generate a COMPLETE, VALID, and EXECUTABLE HTN plan.
"""

_PLANNER_USER_TEMPLATE = """\
Goal: {goal}

Decompose this goal into an execution graph following the schema above.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers."""
    text = text.strip()
    # Handle ```json or just ``` headers
    text = re.sub(r"^```(json)?\s*", "", text, flags=re.IGNORECASE)
    # Handle ``` footer
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _fallback_graph(goal: str) -> PlanGraph:
    """Single-node passthrough plan used when LLM is unavailable."""
    node = PlanNode(
        id="execute_goal",
        task=goal,
        dependencies=[],
        strategy="direct",
        inputs=[],
        outputs={"result": "Output of direct goal execution"},
        dod=DoD("Task completes without error.", "deterministic"),
        uncertainty=0.1,
    )
    return PlanGraph(goal=goal, nodes=[node])


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class PlanTooComplexError(Exception):
    """Raised when a goal decomposes into too many nodes, requiring human refinement."""
    pass

class Planner:
    """
    Converts a free-text goal into a validated PlanGraph.

    Parameters
    ----------
    gateway : optional UnifiedGateway instance.
              If None, the planner loads it lazily from agentx.gateway.
    max_nodes : hard cap on the number of nodes in the decomposed graph.
    """

    # Default score threshold for method-first routing
    DEFAULT_METHOD_THRESHOLD: float = 0.55

    def __init__(
        self,
        gateway=None,
        max_nodes: int = 15,
        method_threshold: float = DEFAULT_METHOD_THRESHOLD,
        use_method_routing: bool = True,
    ):
        self._gateway = gateway
        self.max_nodes = max_nodes
        self.method_threshold = method_threshold
        self.use_method_routing = use_method_routing
        self.knowledge_base = None

    def bias(self, knowledge_base: Any):
        """
        Part E - Knowledge Base injection
        """
        self.knowledge_base = knowledge_base

    def bias_with_strategies(self, trusted: List[Dict[str, Any]], experimental: List[Dict[str, Any]] = None, is_sandbox: bool = False, risk_level: float = 0.5):
        """
        Part C, G & G - Strategy Retrieval, Trusted Memory, and Explore vs Exploit
        """
        from agentx.learning.exploration import exploration_controller
        
        if exploration_controller.should_explore(is_sandbox, risk_level):
            print("[Planner] EXPLORATION triggered. Focusing on experimental strategies.")
            # Part F - Experimental Strategy Pool preferred during exploration
            self.trusted_strategies = []
            self.experimental_strategies = experimental or []
        else:
            self.trusted_strategies = trusted
            self.experimental_strategies = experimental or []

    # -- internal helpers ---------------------------------------------------

    def _get_gateway(self):
        if self._gateway is not None:
            return self._gateway
        try:
            from agentx.gateway import UnifiedGateway
            self._gateway = UnifiedGateway()
        except Exception:
            self._gateway = None
        return self._gateway

    def _call_llm(self, goal: str, retrieved_context: str = "", mode: str = "default", config: Optional[Dict] = None, history: Optional[List[str]] = None) -> Optional[str]:
        gw = self._get_gateway()
        if gw is None:
            return None
            
        try:
            from agentx.planning.methods import MethodLibrary
            methods_str = MethodLibrary.format_for_prompt()
        except Exception:
            methods_str = "{}"
            
        system_prompt = _PLANNER_SYSTEM_PROMPT.format(methods=methods_str, context=retrieved_context)
        mode_prompt = MODE_PROMPTS.get(mode, MODE_PROMPTS["default"])
        
        history_text = ""
        if history:
            history_text = "\n\nPrevious plans generated for this goal (DO NOT DUPLICATE THESE):\n"
            for i, p in enumerate(history):
                history_text += f"Plan {i+1}: {p}\n"
                
        config_text = ""
        if config:
            config_text = f"\n\nGeneration Constraints: {config}"
            
        kb_text = ""
        if hasattr(self, "knowledge_base") and self.knowledge_base:
            if hasattr(self.knowledge_base, "best_patterns") and self.knowledge_base.best_patterns:
                kb_text = f"\n\nKnown Best Patterns (bias your plan towards these proven workflows if relevant):\n{json.dumps(self.knowledge_base.best_patterns[:3], indent=2)}"
                
        strat_text = ""
        if hasattr(self, "trusted_strategies") and self.trusted_strategies:
            strat_text += f"\n\nTRUSTED STRATEGIES (Use these! High success rate):\n{json.dumps(self.trusted_strategies, indent=2)}"
        if hasattr(self, "experimental_strategies") and self.experimental_strategies:
            strat_text += f"\n\nExperimental Strategies (Use with caution):\n{json.dumps(self.experimental_strategies, indent=2)}"
            
        prompt = _PLANNER_USER_TEMPLATE.format(goal=goal) + f"\n\nMode: {mode_prompt}{history_text}{config_text}{kb_text}{strat_text}"
        try:
            # UnifiedGateway.complete(system, user) - str
            response = gw.complete(system=system_prompt, user=prompt)
            return response
        except Exception as exc:
            print(f"[Planner] LLM call failed: {exc}")
            return None

    def _parse_response(self, raw: str, goal: str) -> PlanGraph:
        try:
            cleaned = _strip_markdown_fences(raw)
            data = json.loads(cleaned)
            if not isinstance(data, dict):
                print(f"[Planner] Error: Expected dict, got {type(data)}")
                return _fallback_graph(goal)

            # Enforce max_nodes
            nodes_list = data.get("nodes", [])
            if not isinstance(nodes_list, list):
                nodes_list = []
                
            if len(nodes_list) > self.max_nodes:
                raise PlanTooComplexError(f"Plan exceeds max_nodes ({self.max_nodes}). Refinement required.")
            
            # Extract nodes and handle potential PlanNode errors
            try:
                graph = PlanGraph.from_dict(data)
            except Exception as e:
                print(f"[Planner] PlanGraph.from_dict failed: {e}")
                return _fallback_graph(goal)
                
            # Backfill goal if LLM omitted it
            if not graph.goal:
                graph.goal = goal
            return graph
        except Exception as e:
            print(f"[Planner] Parse Error: {e}")
            print(f"[Planner] Raw output: {repr(raw)}")
            raise e

    # -- method-first routing -----------------------------------------------

    def _try_method_plan(self, goal: str) -> "PlanGraph | None":
        """
        Attempt to build a PlanGraph from the method library without an LLM call.

        Returns a validated PlanGraph if a high-confidence match is found,
        otherwise returns None and the caller should fall back to LLM generation.
        """
        try:
            from agentx.planning.method_retriever import retrieve_methods, method_fit
        except ImportError:
            return None

        candidates = retrieve_methods(goal, top_n=5)
        if not candidates:
            return None

        # candidates is a list of (method, sim)
        scored = [
            (m, method_fit(m, sim, current_state={}))
            for m, sim in candidates
        ]
        best, best_fit = max(scored, key=lambda x: x[1])

        if best_fit < self.method_threshold:
            return None

        template = best.get("plan_template")
        if not template or not template.get("nodes"):
            return None

        try:
            graph = PlanGraph.from_dict(template)
        except Exception as exc:
            print(f"[Planner] Method template parse error: {exc}")
            return None

        # Override goal with the live goal (template may have generic goal text)
        graph.goal = goal

        # Validate before using
        result = DAGValidator.validate(graph)
        if not result.ok:
            print(f"[Planner] Method '{best.get('id')}' failed validation - penalizing and falling back to LLM.")
            try:
                from agentx.planning.method_scorer import update_metrics
                from agentx.planning.method_store import MethodStore
                # Penalize method due to bad DAG
                updated = update_metrics(best, success=False, latency=0.0, uncertainty=1.0)
                MethodStore.upsert(updated)
            except Exception as e:
                print(f"[Planner] Failed to penalize method: {e}")
            return None

        # Tag the graph with the source method id so ReActExecutor can update metrics
        object.__setattr__(graph, "_source_method_id", best["id"]) if hasattr(graph, "__dataclass_fields__") else None
        try:
            graph._source_method_id = best["id"]  # type: ignore[attr-defined]
        except AttributeError:
            pass

        print(
            f"[Planner] Method-first: using cached method '{best.get('id')}' "
            f"(score={best.get('score', 0):.3f}, fit={best_fit:.3f})"
        )
        return graph

    # -- public API ---------------------------------------------------------
    
    def generate_k_plans(self, goal: str, current_state: Dict, k: int = 3, mode: str = "default") -> List[PlanGraph]:
        """
        Part A — Generate Multiple Plans
        """
        from agentx.planning.generator import generate_candidate_plans
        return generate_candidate_plans(goal, current_state, k, mode=mode)

    def _decompose_single(self, goal: str, current_state: Optional[Dict] = None, retrieval_retry: int = 0, mode: str = "default", config: Optional[Dict] = None, history: Optional[List[str]] = None) -> PlanGraph:
        """
        Phase 14: Multi-Plan Planning Architecture.
        Generates, verifies, scores, and selects the optimal plan.
        """
        if not goal or not goal.strip():
            raise ValueError("Planner.decompose: goal must be a non-empty string")
            
        current_state = current_state or {}
        
        print(f"\\n[Planner] Initiating multi-plan generation for goal: '{goal}'")
        
        from agentx.planning.scorer import estimate_complexity, COMPLEXITY_LOW, COMPLEXITY_MEDIUM, score_plan
        from agentx.planning.generator import generate_candidate_plans, revise_plan
        from agentx.planning.verifier import verify_plan
        from agentx.planning.selector import select_plan

        # 1. Complexity Estimation
        complexity = estimate_complexity(goal)
        if complexity == COMPLEXITY_LOW:
            k = 1
        elif complexity == COMPLEXITY_MEDIUM:
            k = 3
        else:
            k = 5
            
        print(f"[Planner] Estimated complexity: {complexity}. Targeting {k} candidates.")

        # 1.5 Hybrid Retrieval
        try:
            from agentx.retrieval.retriever import retrieve
            from agentx.retrieval.validator import validate_answer
            retrieved_items = retrieve(goal)
            
            # 5.2 Reject hallucinated context
            context_text = "\\n".join([f"- {item['content']} (Score: {item.get('score', 0)})" for item in retrieved_items])
            if not validate_answer(goal, context_text):
                if retrieval_retry < 1:
                    print("[Planner] Hallucinated context detected. Retrying retrieval.")
                    return self._decompose_single(goal, current_state, retrieval_retry + 1, mode=mode)
                else:
                    print("[Planner] Repeated hallucination detected. Proceeding without context.")
                    context_text = "No external context available."
            
            context_str = context_text
            print(f"[Planner] Retrieved {len(retrieved_items)} context items via Hybrid Search.")
        except Exception as e:
            print(f"[Planner] [WARN] Retrieval failed: {e}")
            context_str = "No external context available."
            
        # Add retrieved context to current_state so downstream tools (generator) have it if they need it
        current_state["retrieved_context"] = context_str

        # 2. Generate Candidates (Method Retrieval + LLM Generation + Diversity Filter)
        candidates = self.generate_k_plans(goal, current_state, k, mode=mode)
        if not candidates:
            print("[Planner] Failed to generate any candidate plans. Falling back to passthrough.")
            return _fallback_graph(goal)
            
        print(f"[Planner] Generated {len(candidates)} diverse candidate(s).")

        verified_plans = []
        for i, plan_candidate in enumerate(candidates):
            print(f"  -> Evaluating candidate {i+1}...")
            # 3. Verify Plan
            feedback = verify_plan(plan_candidate)
            
            # 4. Critique & Refine if necessary
            if not feedback.get("valid", True):
                plan_candidate = revise_plan(plan_candidate, feedback)
                # Re-verify after revision
                feedback = verify_plan(plan_candidate)
                
            if not feedback.get("valid", True):
                print(f"     [Reject] Candidate {i+1} failed verification.")
                continue
                
            # 5. Cost-Aware Scoring
            is_method = hasattr(plan_candidate, "_source_method_id")
            method_sr = getattr(plan_candidate, "_method_success_rate", 0.5) if is_method else 0.5
            
            score = score_plan(plan_candidate, feedback.get("state_consistency", 0.5), is_method, method_sr)
            risk = feedback.get("risk_score", 0.5)
            
            print(f"     [Accept] Candidate {i+1} - Score: {score:.2f}, Risk: {risk:.2f}")
            verified_plans.append((plan_candidate, score, risk))
            
            # 6. Early Exit Check
            if score > 0.9 and risk < 0.2:
                print(f"[Planner] Early exit triggered by high-confidence, low-risk plan (Candidate {i+1}).")
                return plan_candidate
                
        if not verified_plans:
            print("[Planner] All candidates rejected by Verifier. Falling back to passthrough.")
            return _fallback_graph(goal)

        # 7. Simulation & Selection
        from agentx.planning.simulation import select_best_simulated_plan
        print(f"[Planner] Simulating {len(verified_plans)} verified candidates...")
        
        # verified_plans is list of (plan, score, risk)
        plans_to_simulate = [p[0] for p in verified_plans]
        # For now, we don't have per-plan strategy associations passed explicitly here, 
        # but the simulator handles the strategy if needed.
        
        final_plan = select_best_simulated_plan(plans_to_simulate)
        
        if not final_plan:
            # Part H — Fallback Logic
            print("[Planner] [ALERT] Simulation rejected all verified plans due to high risk.")
            # In a real system, we might call request_user_input() here.
            # For now, we return the plan with the highest score from the verified list as a fallback.
            verified_plans.sort(key=lambda x: x[1], reverse=True)
            return verified_plans[0][0]

        return final_plan

    def decompose(self, goal: str, current_state: Optional[Dict] = None) -> PlanGraph:
        """
        Multi-run consensus planning with Disagreement-Aware Consensus + Minority Veto.
        Supports Phase 22 Synthetic Diversity Beta.
        """
        import time
        import agentx.config
        
        start_time = time.time()
        
        from agentx.observability.metrics import metrics_system
        metrics = metrics_system.get_summary()

        def should_disable_beta(metrics):
            if metrics.get("success_rate_beta", 0.0) < metrics.get("success_rate_stable", 0.0):
                return True
            if metrics.get("latency_increase", 0.0) > 2.0:
                return True
            return False

        if agentx.config.AGENTX_DIVERSITY_BETA and should_disable_beta(metrics):
            print("[Planner] [BETA] Auto-disabling beta due to metric degradation.")
            agentx.config.AGENTX_DIVERSITY_BETA = False

        if not agentx.config.AGENTX_DIVERSITY_BETA:
            return self._original_decompose(goal, current_state)

        from agentx.planning.scorer import estimate_complexity, COMPLEXITY_LOW
        complexity = estimate_complexity(goal)
        if complexity == COMPLEXITY_LOW:
            print("[Planner] [ROUTING] Simple task detected. Forcing stable mode.")
            return self._original_decompose(goal, current_state)

        active_modes = MODES
        if hasattr(metrics_system, "metrics") and "mode_contribution" in metrics_system.metrics:
            mode_scores = metrics_system.metrics["mode_contribution"]
            if mode_scores:
                active_modes = sorted(mode_scores.keys(), key=lambda k: mode_scores[k], reverse=True)[:3]
                if not active_modes:
                    active_modes = MODES

        # Part A - Planner Integration: Experience Memory System
        try:
            from agentx.memory.experience_store import experience_store
            similar = experience_store.retrieve_similar(goal)
            if similar:
                successes = [s for s in similar if s["success"]]
                if len(successes) / len(similar) >= 0.7 and successes:
                    print("[Planner] [MEMORY] High success rate for similar goals. Biasing generation.")
                    if current_state is None: current_state = {}
                    current_state["biased_plan"] = successes[0]["plan_structure"]
        except Exception:
            pass

        print(f"[Planner] [BETA] Running synthetic diversity planning with modes: {active_modes}")
        
        MAX_RETRIES = 2
        plans = []
        for attempt in range(MAX_RETRIES):
            plans = []
            history = []
            for mode in active_modes:
                try:
                    print(f"[Planner] [BETA] Generating plan for mode: {mode}")
                    config = GENERATION_CONFIG.get(mode, GENERATION_CONFIG["default"])
                    if attempt > 0:
                        config = dict(config)
                        config["temperature"] = min(1.0, config["temperature"] + 0.2) # noise
                    
                    import random
                    use_history = history[-2:] if random.random() < 0.5 else None
                    plan = self._decompose_single(goal, current_state, mode=mode, config=config, history=use_history)
                    object.__setattr__(plan, "_generation_mode", mode) if hasattr(plan, "__dataclass_fields__") else None
                    try:
                        plan._generation_mode = mode
                    except AttributeError:
                        pass
                    plans.append(plan)
                    
                    summary = " -> ".join([n.task for n in plan.nodes])
                    history.append(summary)
                except Exception as e:
                    print(f"[Planner] [BETA] Mode {mode} generation failed: {e}")
            
            if diversity_collapse_score(plans) > 0.75:
                print("[Planner] [BETA] Diversity collapse detected. Triggering regeneration with noise.")
                if hasattr(metrics_system, "metrics"):
                    metrics_system.metrics.setdefault("diversity_collapse_events", 0)
                    metrics_system.metrics["diversity_collapse_events"] += 1
                    metrics_system.metrics.setdefault("regeneration_triggered", 0)
                    metrics_system.metrics["regeneration_triggered"] += 1
                continue # trigger regeneration
            
            original_count = len(plans)
            plans = enforce_diversity(plans)
            print(f"[Planner] [BETA] Diversity enforcement: {len(plans)}/{original_count} plans retained (Attempt {attempt+1}).")
            
            if len(plans) >= 2:
                break
        
        if not plans:
            return _fallback_graph(goal)

        if len(plans) == 1:
            print("[Planner] [SAFETY] Diversity failed → fallback to stable")
            return self._original_decompose(goal, current_state)

        # Part 4: Integration with Existing System (Disagreement, Critic, Scorer)
        from agentx.decision.disagreement import disagreement_score, detect_conflicts, minority_veto, update_disagreement_metrics
        from agentx.planning.verifier import verify_plan
        from agentx.planning.selector import select_plan
        from agentx.decision.critic import compare_reasoning, critique_plan, critic_score

        d_score = disagreement_score(plans)
        conflicts = detect_conflicts(plans)
        veto = minority_veto(plans)
        
        c_score_collapse = diversity_collapse_score(plans)
        
        # Part 2: Hard Beta Safety Gates
        if d_score > 0.7 or c_score_collapse > 0.75 or len(plans) < 2:
            print("[Planner] [SAFETY] Safety gate triggered → fallback to stable")
            if hasattr(metrics_system, "metrics"):
                metrics_system.metrics.setdefault("fallback_triggered", 0)
                metrics_system.metrics["fallback_triggered"] += 1
            return self._original_decompose(goal, current_state)

        # Part 6: Metrics
        latency = time.time() - start_time
        beta_metrics = {
            "diversity_score": avg_pairwise_distance(plans),
            "plan_variance": structural_variance(plans),
            "latency": latency
        }
        # Record beta metrics if the tracking method exists
        if hasattr(metrics_system, "record_beta_metrics"):
            metrics_system.record_beta_metrics(beta_metrics)

        # Resume standard consensus/selection logic
        shared_info = compare_reasoning(plans)
        shared = shared_info.get("shared_patterns", {})
        
        has_shared_error = any(count == len(plans) for count in shared.values())
        update_disagreement_metrics(d_score, veto, True) # True for beta_active

        if d_score > 0.5 or conflicts or veto or has_shared_error:
            verified_plans = []
            for p in plans:
                fb = verify_plan(p)
                risk = fb.get("risk_score", 0.5)
                verified_plans.append((p, 1.0 - risk, risk))
            best_plan = select_plan(verified_plans)
        elif d_score > 0.3:
            critiques = []
            for plan in plans:
                critique = critique_plan(plan, current_state or {})
                critiques.append((plan, critique))
            
            from agentx.planning.scorer import score_plan
            scored = []
            for plan, critique in critiques:
                diversity_bonus = 0.0
                if len(plans) > 1:
                    sims = [semantic_similarity(plan, op) for op in plans if op is not plan]
                    diversity_bonus = 1.0 - (sum(sims) / len(sims))

                s = score_plan(plan, 0.5)
                s += critic_score(plan, critique)
                s += diversity_bonus * 0.5 # Add diversity bonus
                scored.append((plan, s, 0.5))
            best_plan = select_plan(scored)
        else:
            def plan_similarity_score(p: PlanGraph) -> float:
                sim_total = 0.0
                for other_p in plans:
                    if other_p is p: continue
                    sim_total += similarity(p, other_p)
                return sim_total
            plans.sort(key=plan_similarity_score, reverse=True)
            best_plan = plans[0]

        # Inject confidence
        best_critique = critique_plan(best_plan, current_state or {})
        c_score = critic_score(best_plan, best_critique)
        fb = verify_plan(best_plan)
        v_score = 1.0 - fb.get("risk_score", 0.5)
        confidence = max(0.0, (1.0 - d_score) * c_score * v_score)
        
        object.__setattr__(best_plan, "confidence", confidence) if hasattr(best_plan, "__dataclass_fields__") else None
        try:
            best_plan.confidence = confidence
        except Exception: pass
        
        # Phone Visibility Layer
        status_info = {
            "mode": "beta",
            "plans": len(plans),
            "selected": getattr(best_plan, "_generation_mode", "unknown"),
            "confidence": confidence,
            "diversity_score": beta_metrics.get("diversity_score", 0.0),
            "status": "running"
        }
        
        if confidence < 0.6 or d_score > 0.7:
            status_info["status"] = "AWAITING_APPROVAL"
            status_info["reason"] = "low confidence or high disagreement"
            
        object.__setattr__(best_plan, "phone_status", status_info) if hasattr(best_plan, "__dataclass_fields__") else None
        try:
            best_plan.phone_status = status_info
        except Exception: pass
        
        # Part 4: Mandatory Logging
        log_entry = {
            "goal": goal,
            "mode": "beta",
            "plans_generated": len(plans),
            "selected_mode": getattr(best_plan, "_generation_mode", "unknown"),
            "diversity_score": beta_metrics.get("diversity_score", 0.0),
            "collapse_score": c_score_collapse,
            "fallback_triggered": False,
            "latency": time.time() - start_time
        }
        if hasattr(metrics_system, "log_execution"):
            metrics_system.log_execution(log_entry)
        else:
            metrics_system.metrics.setdefault("execution_logs", []).append(log_entry)
        
        # Part 6: Track mode contribution
        sel_mode = getattr(best_plan, "_generation_mode", "unknown")
        if hasattr(metrics_system, "metrics"):
            mc = metrics_system.metrics.setdefault("mode_contribution", {})
            mc[sel_mode] = mc.get(sel_mode, 0) + 1

        # Phase 26: Exploration vs Exploitation
        import random
        try:
            from agentx.rl.policy_store import policy_store
            if random.random() < policy_store.exploration_rate and len(plans) > 1:
                # Explore: pick a random plan instead of the best one
                print("[Planner] [RL] Exploring new plan instead of best plan.")
                candidates = [p for p in plans if p != best_plan]
                if candidates:
                    best_plan = random.choice(candidates)
                    
            # Part J - Telemetry + Metrics (Track)
            if hasattr(metrics_system, "metrics"):
                metrics_system.metrics["avg_reward"] = policy_store.avg_reward
                metrics_system.metrics["exploration_rate"] = policy_store.exploration_rate
                
            # Part K - Drift Control (Critical)
            # if success rate drops significantly, we reset policy
            if hasattr(metrics_system, "metrics"):
                success_rate_beta = metrics_system.metrics.get("success_rate_beta", 1.0)
                if success_rate_beta < 0.8: # drop > 20%
                    policy_store.reset_policy()
        except Exception:
            pass

        return best_plan

    def _original_decompose(self, goal: str, current_state: Optional[Dict] = None) -> PlanGraph:
        """
        Multi-run consensus planning (Original Phase 21.6 logic).
        """
        import random
        random.seed(42)

        print("[Planner] Running original multi-run consensus planning (3 runs)")
        plans = []
        for _ in range(3):
            try:
                plan = self._decompose_single(goal, current_state)
                plans.append(plan)
            except Exception as e:
                print(f"[Planner] decompose run failed: {e}")
        
        if not plans:
            return _fallback_graph(goal)

        if len(plans) == 1:
            return plans[0]

        from agentx.decision.disagreement import disagreement_score, detect_conflicts, minority_veto, update_disagreement_metrics
        from agentx.planning.verifier import verify_plan
        from agentx.planning.selector import select_plan
        
        score = disagreement_score(plans)
        conflicts = detect_conflicts(plans)
        veto = minority_veto(plans)
        
        from agentx.decision.critic import compare_reasoning, critique_plan, critic_score
        shared_info = compare_reasoning(plans)
        shared = shared_info.get("shared_patterns", {})
        
        has_shared_error = any(count == len(plans) for count in shared.values())
        update_disagreement_metrics(score, veto, False)
        
        if score > 0.5 or conflicts or veto or has_shared_error:
            verified_plans = []
            for p in plans:
                fb = verify_plan(p)
                risk = fb.get("risk_score", 0.5)
                verified_plans.append((p, 1.0 - risk, risk))
            best_plan = select_plan(verified_plans)
        elif score > 0.3:
            critiques = []
            for plan in plans:
                critique = critique_plan(plan, current_state or {})
                critiques.append((plan, critique))
            
            from agentx.planning.scorer import score_plan
            scored = []
            for plan, critique in critiques:
                s = score_plan(plan, 0.5)
                s += critic_score(plan, critique)
                scored.append((plan, s, 0.5))
            best_plan = select_plan(scored)
        else:
            def plan_similarity_score(p: PlanGraph) -> float:
                sim_score = 0.0
                p_nodes = {n.id for n in p.primitive_nodes()}
                for other_p in plans:
                    if other_p is p: continue
                    other_nodes = {n.id for n in other_p.primitive_nodes()}
                    inter = len(p_nodes.intersection(other_nodes))
                    union = len(p_nodes.union(other_nodes))
                    sim_score += inter / union if union > 0 else 0.0
                return sim_score
            plans.sort(key=plan_similarity_score, reverse=True)
            best_plan = plans[0]

        # Inject confidence
        best_critique = critique_plan(best_plan, current_state or {})
        c_score = critic_score(best_plan, best_critique)
        fb = verify_plan(best_plan)
        v_score = 1.0 - fb.get("risk_score", 0.5)
        confidence = max(0.0, (1.0 - score) * c_score * v_score)
        
        object.__setattr__(best_plan, "confidence", confidence) if hasattr(best_plan, "__dataclass_fields__") else None
        try:
            best_plan.confidence = confidence
        except Exception: pass

        return best_plan
