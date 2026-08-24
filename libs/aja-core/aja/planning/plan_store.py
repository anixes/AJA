import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict
from aja.memory.manager import MemoryManager, get_memory_manager
from aja.planning.models import PlanGraph, PlanNode

logger = logging.getLogger(__name__)

_manager = get_memory_manager()


def _embed_goal_or_zero(goal: str) -> tuple[list[float], str]:
    """Embeds the plan goal via the shared embedding service.

    Returns (vector, model_name). Never raises: when the backend is
    unavailable the row keeps an all-zero vector tagged "none" so consumers
    can distinguish "never embedded" from a real semantic vector.
    """
    if not (goal or "").strip():
        return [0.0] * 384, "none"
    try:
        from aja.embeddings.service import get_embedding_service

        service = get_embedding_service()
        vector = service.embed(goal)
        return vector, service.get_model_name()
    except Exception as e:  # last resort: write-path must stay non-fatal
        logger.warning(
            "Embedding backend unavailable; storing tagged zero vector for "
            "plan goal '%s': %s",
            goal[:80],
            e,
        )
        return [0.0] * 384, "none"


def _encode_steps(nodes: list[dict], embedding_model: str) -> str:
    """Serializes nodes plus embedding provenance.

    The core_plans schema has no metadata column, so the provenance rides
    inside the existing steps_json payload as a wrapper dict.
    """
    return json.dumps({"nodes": list(nodes), "embedding_model": embedding_model})


def _decode_steps(raw) -> tuple[list[dict], str]:
    """Parses steps_json, tolerating both the wrapped dict shape and the
    legacy bare-list shape written by older versions."""
    try:
        payload = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return [], "none"
    if isinstance(payload, dict):
        nodes = payload.get("nodes") or []
        if not isinstance(nodes, list):
            nodes = []
        model = payload.get("embedding_model") or "none"
        return nodes, str(model)
    if isinstance(payload, list):
        return payload, "none"
    return [], "none"

class PlanStore:
    """
    High-performance PlanStore powered by LanceDB/Arrow.
    Provides semantic goal search and zero-copy plan coordination.
    """
    @classmethod
    def save(cls, plan_id: str, graph: PlanGraph) -> None:
        table = _manager.get_table("core_plans")
        status = cls._derive_status(graph)
        nodes = [n.to_dict() for n in graph.nodes]
        now = datetime.now(timezone.utc).isoformat()

        # Upsert logic via Arrow filter
        existing = table.search().where(f"plan_id = '{plan_id}'").limit(1).to_list()

        if existing:
            # Preserve the previously recorded embedding provenance; the
            # vector column itself is not rewritten on update.
            _, prev_model = _decode_steps(existing[0].get("steps_json"))
            table.update(where=f"plan_id = '{plan_id}'", values={
                "status": status,
                "steps_json": _encode_steps(nodes, prev_model),
                "created_at": now # Should be updated_at in a real schema, using created_at for POC
            })
        else:
            text = graph.goal or ""
            vector, model_name = _embed_goal_or_zero(text)
            row = [{
                "plan_id": plan_id,
                "goal": graph.goal,
                "steps_json": _encode_steps(nodes, model_name),
                "status": status,
                "created_at": now,
                "vector": vector
            }]
            table.add(row)

    @classmethod
    def load(cls, plan_id: str) -> Optional[PlanGraph]:
        table = _manager.get_table("core_plans")
        results = table.search().where(f"plan_id = '{plan_id}'").limit(1).to_list()

        if not results:
            return None

        row = results[0]
        nodes_raw, _ = _decode_steps(row.get("steps_json"))
        nodes = [PlanNode.from_dict(n) for n in nodes_raw]
        return PlanGraph(goal=row["goal"], nodes=nodes)

    @classmethod
    def record_repair(cls, plan_id: str, node_id: str, action: str, metadata: Optional[Dict] = None) -> None:
        """Compatibility hook for ReActExecutor repair telemetry."""
        table = _manager.get_table("core_tool_executions")
        now = datetime.now(timezone.utc).isoformat()
        table.add([{
            "execution_id": f"repair-{plan_id}-{node_id}-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            "task_id": node_id,
            "tool_name": "plan_repair",
            "args_json": json.dumps({"plan_id": plan_id, "action": action, "metadata": metadata or {}}),
            "status": "RECORDED",
            "output_summary": f"Repair action recorded: {action}",
            "created_at": now,
        }])

    @staticmethod
    def _derive_status(graph: PlanGraph) -> str:
        statuses = {n.status for n in graph.nodes}
        if not statuses: return "PENDING"
        if statuses == {"COMPLETED"}: return "COMPLETED"
        if "RUNNING" in statuses: return "RUNNING"
        if "FAILED" in statuses: return "FAILED"
        return "PENDING"
