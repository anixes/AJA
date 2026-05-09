"""
agentx/planning/plan_store.py
==============================
Phase 11 - Plan Persistence Layer.

Stores plans and their node states in the existing agentx SQLite database
(`.agentx/aja_secretary.sqlite3`) by adding two new tables:

  plans      - top-level plan records
  plan_nodes - per-node state snapshots

Design principles
-----------------
* Reuses the existing DB_PATH from `agentx.persistence.tasks`.
* Idempotent schema init - safe to call at import time.
* Each save() call is a full upsert; no partial-update complexity.
* JSON columns for structured fields (nodes list, repair_history).
"""

from __future__ import annotations

import json
import sqlite3
import os
from datetime import datetime, timezone
from typing import List, Optional

from agentx.planning.models import PlanGraph, PlanNode


# Reuse the existing DB path env-var
DB_PATH = os.environ.get("AGENTX_DB_PATH", os.path.join(".agentx", "aja_secretary.sqlite3"))


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plans (
    plan_id     TEXT PRIMARY KEY,
    goal        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'PENDING',
    nodes_json  TEXT NOT NULL DEFAULT '[]',
    created_at  TIMESTAMP NOT NULL,
    updated_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_repairs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id     TEXT NOT NULL,
    node_id     TEXT NOT NULL,
    attempt     INTEGER NOT NULL,
    failure_kind TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    notes       TEXT,
    timestamp   TIMESTAMP NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
);

CREATE INDEX IF NOT EXISTS idx_plans_status  ON plans (status);
CREATE INDEX IF NOT EXISTS idx_repairs_plan  ON plan_repairs (plan_id);
"""


def _init_schema():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_SCHEMA_SQL)


# Initialise once at import time (idempotent)
try:
    _init_schema()
except Exception as _e:
    print(f"[PlanStore] Schema init warning: {_e}")


# ---------------------------------------------------------------------------
# PlanStore
# ---------------------------------------------------------------------------

class PlanStore:
    """
    CRUD interface for plan persistence.

    All methods are class-level; no instance state required.
    """

    # -- save / upsert ------------------------------------------------------

    @classmethod
    def save(cls, plan_id: str, graph: PlanGraph) -> None:
        """
        Upsert a plan record.  Derives overall status from node statuses.
        """
        status = cls._derive_status(graph)
        nodes_json = json.dumps([n.to_dict() for n in graph.nodes])
        now = _iso_now()

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO plans (plan_id, goal, status, nodes_json, created_at, updated_at)
                VALUES (-, -, -, -, -, -)
                ON CONFLICT(plan_id) DO UPDATE SET
                    status     = excluded.status,
                    nodes_json = excluded.nodes_json,
                    updated_at = excluded.updated_at
                """,
                (plan_id, graph.goal, status, nodes_json, now, now),
            )

    # -- load ---------------------------------------------------------------

    @classmethod
    def load(cls, plan_id: str) -> Optional[PlanGraph]:
        """Return a PlanGraph from the DB, or None if not found."""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM plans WHERE plan_id = -", (plan_id,)
                ).fetchone()
        except Exception as exc:
            print(f"[PlanStore] load() error: {exc}")
            return None

        if row is None:
            return None

        try:
            nodes_raw = json.loads(row["nodes_json"])
            nodes = [PlanNode.from_dict(n) for n in nodes_raw]
            graph = PlanGraph(goal=row["goal"], nodes=nodes)
            return graph
        except Exception as exc:
            print(f"[PlanStore] Failed to deserialise plan '{plan_id}': {exc}")
            return None

    # -- record repairs -----------------------------------------------------

    @classmethod
    def record_repair(
        cls,
        plan_id: str,
        node_id: str,
        attempt: int,
        failure_kind: str,
        action_taken: str,
        notes: str = "",
    ) -> None:
        now = _iso_now()
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    """
                    INSERT INTO plan_repairs
                        (plan_id, node_id, attempt, failure_kind, action_taken, notes, timestamp)
                    VALUES (-, -, -, -, -, -, -)
                    """,
                    (plan_id, node_id, attempt, failure_kind, action_taken, notes, now),
                )
        except Exception as exc:
            print(f"[PlanStore] record_repair() error: {exc}")

    # -- list active plans --------------------------------------------------

    @classmethod
    def list_active(cls) -> List[dict]:
        """Return all plans not yet in a terminal state."""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT plan_id, goal, status, created_at, updated_at "
                    "FROM plans WHERE status NOT IN ('COMPLETED', 'FAILED') "
                    "ORDER BY updated_at DESC"
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[PlanStore] list_active() error: {exc}")
            return []

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _derive_status(graph: PlanGraph) -> str:
        statuses = {n.status for n in graph.nodes}
        if not statuses:
            return "PENDING"
        if statuses == {"COMPLETED"}:
            return "COMPLETED"
        if "RUNNING" in statuses:
            return "RUNNING"
        if "FAILED" in statuses and "PENDING" not in statuses and "RUNNING" not in statuses:
            return "FAILED"
        return "PENDING"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
