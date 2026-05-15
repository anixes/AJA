"""
agentx/planning/selector.py
============================
Phase 14 - Selector Agent.

Responsible for taking a list of generated, scored, and verified plans
and selecting the optimal one. Balances risk and score margin.
"""

from __future__ import annotations

from typing import List, Tuple
from agentx.planning.models import PlanGraph

def select_plan(plans_with_metadata: List[Tuple[PlanGraph, float, float]]) -> PlanGraph:
    """
    Selects the best plan from a list of (plan, score, risk) tuples.
    
    Decision rules:
    - If the highest score plan is significantly better than others (margin > 0.15) 
      AND its risk is acceptable (< 0.7), pick it.
    - Otherwise, if margins are small, pick the safest plan (lowest risk).
    """
    if not plans_with_metadata:
        raise ValueError("Cannot select from an empty list of plans.")
        
    if len(plans_with_metadata) == 1:
        return plans_with_metadata[0][0]
        
    # Sort by score descending
    plans_with_metadata.sort(key=lambda x: x[1], reverse=True)
    
    best_plan, best_score, best_risk = plans_with_metadata[0]
    second_best_score = plans_with_metadata[1][1]
    
    margin = best_score - second_best_score
    
    if margin > 0.15 and best_risk < 0.7:
        print(f"[Selector] Selecting highest score plan (Score: {best_score:.2f}, Risk: {best_risk:.2f}, Margin: {margin:.2f})")
        return best_plan
        
    # Otherwise pick the safest plan among the top contenders (say, top 3 or those within 0.2 of best score)
    contenders = [p for p in plans_with_metadata if best_score - p[1] <= 0.2]
    
    # Sort contenders by risk ascending
    contenders.sort(key=lambda x: x[2])
    
    safest_plan, safest_score, safest_risk = contenders[0]
    print(f"[Selector] Selecting safest plan (Score: {safest_score:.2f}, Risk: {safest_risk:.2f})")
    
    return safest_plan
