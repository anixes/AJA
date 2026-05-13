"""
agentx/planning/method_store.py
================================
Phase 12 - Persistent Method Store.

Each method entry has a rich schema including metrics, score, and a full
plan_template (a PlanGraph dict) that the planner can instantiate directly.

Schema
------
{
  "id": "snake_case_id",
  "task_type": "string",
  "pattern": "semantic description of goal pattern",
  "plan_template": { "goal": "...", "nodes": [...] },
  "embedding": [ ... ],
  "metrics": {
    "success_rate": float,
    "avg_uncertainty": float,
    "avg_latency": float,
    "reuse_count": int,
    "stability": float
  },
  "score": float,
  "last_used": "ISO timestamp | null"
}
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, List, Optional

from agentx.embeddings.index import VectorIndex
from agentx.embeddings.service import EmbeddingService

METHODS_FILE = os.path.join(".agentx", "methods.json")


class MethodStore:
    """
    Persistent storage for HTN decomposition methods.
    Maintains an in-memory VectorIndex that stays perfectly synced with the persistent file.

    All mutations are atomic: writes go to a temp file then rename,
    so a crash mid-write never corrupts the store.
    """

    _index: Optional[VectorIndex] = None


    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_dir() -> None:
        os.makedirs(".agentx", exist_ok=True)

    @classmethod
    def _methods_path(cls) -> str:
        return METHODS_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def get_index(cls) -> VectorIndex:
        """Get the singleton VectorIndex, loading methods if necessary."""
        if cls._index is None:
            # load() will trigger index rebuilding
            cls.load()
            if cls._index is None:
                # If load() returned empty, create an empty index
                cls._index = VectorIndex()
        return cls._index

    @classmethod
    def load(cls) -> List[Dict]:
        """
        Load all methods from disk, returning them.
        Ensures the VectorIndex is built and methods have embeddings.

        Returns an empty list if the file is missing or corrupted
        (never raises).
        """
        path = cls._methods_path()
        methods = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    methods = data
                elif isinstance(data, dict):
                    # Legacy format: dict of name -> subtasks
                    methods = cls._migrate_legacy(data)
            except Exception as exc:
                print(f"[MethodStore] Failed to load methods: {exc}")

        # Phase 13: Ensure embeddings exist for all methods
        migrated = False
        svc = None
        for m in methods:
            if "embedding" not in m or not isinstance(m["embedding"], list):
                if svc is None:
                    svc = EmbeddingService()
                print(f"[MethodStore] Generating missing embedding for method '{m.get('id')}'...")
                m["embedding"] = svc.embed(m.get("pattern", ""))
                migrated = True

        if migrated:
            cls._save_raw(methods)

        # Rebuild index
        cls._index = VectorIndex()
        for m in methods:
            mid = m.get("id")
            emb = m.get("embedding")
            if mid and emb:
                cls._index.add(mid, emb)

        return methods

    @classmethod
    def _save_raw(cls, methods: List[Dict]) -> bool:
        """Internal save method without rebuilding index."""
        cls._ensure_dir()
        path = cls._methods_path()
        try:
            dir_name = os.path.dirname(os.path.abspath(path))
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=dir_name,
                delete=False,
                suffix=".tmp",
            ) as tmp:
                json.dump(methods, tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, path)
            return True
        except Exception as exc:
            print(f"[MethodStore] Failed to save methods: {exc}")
            return False

    @classmethod
    def save(cls, methods: List[Dict]) -> bool:
        """
        Atomically persist methods to disk and sync the VectorIndex.

        Returns True on success, False on error.
        """
        success = cls._save_raw(methods)
        if success:
            cls._index = VectorIndex()
            for m in methods:
                mid = m.get("id")
                emb = m.get("embedding")
                if mid and emb:
                    cls._index.add(mid, emb)
        return success

    @classmethod
    def get_by_id(cls, method_id: str) -> Optional[Dict]:
        """Return a single method by ID, or None if not found."""
        for m in cls.load():
            if m.get("id") == method_id:
                return m
        return None

    @classmethod
    def upsert(cls, method: Dict) -> bool:
        """
        Add or replace a method by its ``id`` field.

        Returns True on success.
        """
        mid = method.get("id")
        if not mid:
            print("[MethodStore] upsert: method has no 'id' field — skipping.")
            return False
        methods = cls.load()
        replaced = False
        for i, m in enumerate(methods):
            if m.get("id") == mid:
                methods[i] = method
                replaced = True
                break
        if not replaced:
            methods.append(method)
        return cls.save(methods)

    @classmethod
    def remove(cls, method_id: str) -> bool:
        """Remove a method by ID. Returns True if found and removed."""
        methods = cls.load()
        before = len(methods)
        methods = [m for m in methods if m.get("id") != method_id]
        if len(methods) == before:
            return False
        return cls.save(methods)

    @classmethod
    def count(cls) -> int:
        """Return number of stored methods."""
        return len(cls.load())

    @classmethod
    def format_for_prompt(cls) -> str:
        """
        Format method patterns for injection into the planner LLM prompt.

        Returns a compact JSON string of {id: pattern} pairs.
        """
        methods = cls.load()
        if not methods:
            return "{}"
        summary = {m["id"]: m.get("pattern", "") for m in methods if m.get("id")}
        return json.dumps(summary, indent=2)

    # ------------------------------------------------------------------
    # Migration helper
    # ------------------------------------------------------------------

    @staticmethod
    def _migrate_legacy(old: Dict) -> List[Dict]:
        """
        Migrate Phase 11 flat dict format to Phase 12 rich schema.

        Old format: { "method_name": ["subtask1", "subtask2", ...] }
        """
        migrated = []
        for name, subtasks in old.items():
            migrated.append({
                "id": name,
                "task_type": "unknown",
                "pattern": name.replace("_", " "),
                "plan_template": {"goal": name, "nodes": []},
                "metrics": {
                    "success_rate": 0.5,
                    "avg_uncertainty": 0.5,
                    "avg_latency": 0.0,
                    "reuse_count": 0,
                    "stability": 0.5,
                },
                "score": 0.4,
                "last_used": None,
            })
        print(f"[MethodStore] Migrated {len(migrated)} legacy methods to Phase 12 schema.")
        return migrated
