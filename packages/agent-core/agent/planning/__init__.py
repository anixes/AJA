"""
agent/planning/__init__.py
============================
Phase 12 - Planning Layer Package.

Exports the public surface for the planning subsystem:
  PlanNode, PlanGraph      - data model
  Planner                  - goal -> PlanGraph (method-first then LLM-backed)
  DAGValidator             - cycle detection + dependency check
  Scheduler                - topological execution order
  ExecutionBridge          - calls engine.run per node
  Replanner                - failure recovery
  MethodStore              - persistent method library
  MethodScorer             - scoring + EWA metric updates
  MethodRetriever          - TF-IDF retrieval + fit scoring
  MethodLearner            - controlled method extraction from successful plans
  MethodPruner             - library pruning + deduplication
"""

from agent.planning.models import PlanNode, PlanGraph
from agent.planning.planner import Planner
from agent.planning.dag_validator import DAGValidator
from agent.planning.scheduler import Scheduler
from agent.planning.execution_bridge import ExecutionBridge
from agent.planning.replanner import Replanner
from agent.planning.method_store import MethodStore
from agent.planning.method_scorer import score_method, update_metrics
from agent.planning.method_retriever import retrieve_methods, method_fit
from agent.planning.method_learner import learn_method, is_eligible
from agent.planning.method_pruner import prune_methods

__all__ = [
    "PlanNode",
    "PlanGraph",
    "Planner",
    "DAGValidator",
    "Scheduler",
    "ExecutionBridge",
    "Replanner",
    # Phase 12
    "MethodStore",
    "score_method",
    "update_metrics",
    "retrieve_methods",
    "method_fit",
    "learn_method",
    "is_eligible",
    "prune_methods",
]
