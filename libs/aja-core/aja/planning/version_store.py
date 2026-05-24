"""
aja/planning/version_store.py
==================================
Wave 2 - Plan Versioning System

Persists a linked chain of PlanVersion snapshots for a given plan_id.

Layout on disk:
    .aja/plan_versions/<plan_id>/
        v_<version_id>.json    ← one file per version
        index.json             ← ordered list of version IDs (chain)

Public API
----------
    VersionStore.cut(plan_id, graph, parent_id, label) -> PlanVersion
    VersionStore.load(plan_id, version_id)             -> PlanVersion
    VersionStore.chain(plan_id)                        -> List[PlanVersion]  (oldest → newest)
    VersionStore.latest(plan_id)                       -> PlanVersion | None
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from aja.config import PROJECT_ROOT
from aja.planning.models import PlanGraph, PlanVersion

# Storage root
_VERSIONS_DIR = PROJECT_ROOT / ".aja" / "plan_versions"


def _plan_dir(plan_id: str):
    d = _VERSIONS_DIR / plan_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path(plan_id: str):
    return _plan_dir(plan_id) / "index.json"


def _load_index(plan_id: str) -> List[str]:
    path = _index_path(plan_id)
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _save_index(plan_id: str, ids: List[str]) -> None:
    with open(_index_path(plan_id), "w") as f:
        json.dump(ids, f, indent=2)


class VersionStore:
    """Namespace for plan version persistence operations."""

    @staticmethod
    def cut(
        plan_id: str,
        graph: PlanGraph,
        parent_id: Optional[str] = None,
        label: str = "initial",
    ) -> PlanVersion:
        """
        Create and persist a new PlanVersion snapshot.

        Parameters
        ----------
        plan_id  : The plan this version belongs to.
        graph    : Current PlanGraph state to snapshot.
        parent_id: Version ID of the preceding version.
        label    : Reason for the cut — 'initial', 'repair', 'hitl', etc.

        Returns
        -------
        PlanVersion
        """
        # Deep-copy the graph via serialise → deserialise
        graph_copy = PlanGraph.from_dict(graph.to_dict())

        version = PlanVersion(
            parent=parent_id,
            plan=graph_copy,
            label=label,
        )

        # Write version file
        v_path = _plan_dir(plan_id) / f"v_{version.id}.json"
        with open(v_path, "w") as f:
            json.dump(version.to_dict(), f, indent=2)

        # Append to index
        chain = _load_index(plan_id)
        chain.append(version.id)
        _save_index(plan_id, chain)

        return version

    @staticmethod
    def load(plan_id: str, version_id: str) -> Optional[PlanVersion]:
        """Load a specific version by ID."""
        v_path = _plan_dir(plan_id) / f"v_{version_id}.json"
        if not v_path.exists():
            return None
        with open(v_path) as f:
            return PlanVersion.from_dict(json.load(f))

    @staticmethod
    def chain(plan_id: str) -> List[PlanVersion]:
        """Return all versions for *plan_id* ordered oldest → newest."""
        ids = _load_index(plan_id)
        versions = []
        for vid in ids:
            v = VersionStore.load(plan_id, vid)
            if v:
                versions.append(v)
        return versions

    @staticmethod
    def latest(plan_id: str) -> Optional[PlanVersion]:
        """Return the most recent version, or None if no versions exist."""
        ids = _load_index(plan_id)
        if not ids:
            return None
        return VersionStore.load(plan_id, ids[-1])
