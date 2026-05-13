"""
agentx/planning/__init__.py
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

from agentx.planning.models import PlanNode, PlanGraph
from agentx.planning.planner import Planner
from agentx.planning.dag_validator import DAGValidator
from agentx.planning.scheduler import Scheduler
from agentx.planning.execution_bridge import ExecutionBridge
from agentx.planning.replanner import Replanner
from agentx.planning.method_store import MethodStore
from agentx.planning.method_scorer import score_method, update_metrics
from agentx.planning.method_retriever import retrieve_methods, method_fit
from agentx.planning.method_learner import learn_method, is_eligible
from agentx.planning.method_pruner import prune_methods

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
