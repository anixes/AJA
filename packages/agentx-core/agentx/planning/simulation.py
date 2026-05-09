import json
from typing import Dict, List, Any, Optional

from agentx.planning.models import PlanGraph
from agentx.llm import get_gateway_for_model
import agentx.config

class SimulationResult:
    def __init__(self, success_probability: float, risk: float, latency: float, complexity: float, feedback: str):
        self.success_probability = success_probability
        self.risk = risk
        self.latency = latency
        self.complexity = complexity
        self.feedback = feedback

    def score(self) -> float:
        """
        Part C — Score Plans
        """
        success_weight = 0.5
        risk_weight = 0.3
        latency_weight = 0.2
        
        return (
            success_weight * self.success_probability -
            risk_weight * self.risk -
            latency_weight * self.latency
        )

def simulate_plan(plan: PlanGraph, strategy: Optional[Dict[str, Any]] = None) -> SimulationResult:
    """
    Part B & E — Simulate each plan & Strategy-aware simulation
    """
    model_name = agentx.config.AGENTX_PLANNER_MODEL
    gw, mapped_model = get_gateway_for_model(model_name)
    
    plan_json = json.dumps(plan.to_dict(), indent=2)
    strategy_info = f"\nApplied Strategy: {json.dumps(strategy, indent=2)}" if strategy else ""
    
    system = """You are AgentX Plan Simulator.
Predict the outcome of the given plan before it is executed.
Analyze dependencies, tool requirements, and potential failure points.
Return JSON ONLY:
{
    "success_probability": 0.0-1.0,
    "risk": 0.0-1.0,
    "latency": 0.0-1.0 (estimated execution time normalized),
    "complexity": 0.0-1.0,
    "predicted_failures": ["list of what might go wrong"],
    "feedback": "overall reasoning"
}"""
    prompt = f"Plan to simulate: {plan_json}{strategy_info}"
    
    try:
        raw = gw.chat(model=mapped_model, prompt=prompt, system=system)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        
        data = json.loads(raw)
        return SimulationResult(
            success_probability=data.get("success_probability", 0.5),
            risk=data.get("risk", 0.5),
            latency=data.get("latency", 0.5),
            complexity=data.get("complexity", 0.5),
            feedback=data.get("feedback", "")
        )
    except Exception as e:
        print(f"[Simulator] Error: {e}")
        return SimulationResult(0.5, 0.5, 0.5, 0.5, "Simulation failed.")

def select_best_simulated_plan(plans: List[PlanGraph], strategies: List[Dict[str, Any]] = None) -> PlanGraph:
    """
    Part D & G — Select best plan & Diversity
    """
    if not plans:
        return None
    if len(plans) == 1:
        return plans[0]

    results = []
    for i, plan in enumerate(plans):
        # Match strategy if available
        strategy = strategies[i] if strategies and len(strategies) > i else None
        sim = simulate_plan(plan, strategy)
        
        # Part F — Failure Prediction
        threshold = 0.4 # success_probability threshold
        if sim.success_probability < threshold:
            print(f"[Simulator] Rejecting candidate {i+1} due to high failure prediction ({sim.success_probability:.2f})")
            continue
            
        results.append((plan, sim.score()))
        print(f"[Simulator] Candidate {i+1} simulated score: {results[-1][1]:.2f}")

    if not results:
        return None
        
    # Part D - Select best plan
    results.sort(key=lambda x: x[1], reverse=True)
    return results[0][0]
