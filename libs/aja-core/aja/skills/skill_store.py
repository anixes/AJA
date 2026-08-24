import json
import logging
import uuid
import lancedb
import pyarrow as pa
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Dict
from aja.config import PROJECT_ROOT, DATA_DIR
from aja.memory.manager import list_tables_defensive

logger = logging.getLogger(__name__)

# Reserved tag prefix used inside tags_json to record embedding provenance
# (the aja_skills schema has no dedicated metadata column).
EMBEDDING_MODEL_TAG_PREFIX = "embedding_model:"
EMBEDDING_MODEL_NONE = EMBEDDING_MODEL_TAG_PREFIX + "none"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def _embed_or_zero(text: str) -> tuple[list[float], str]:
    """Embeds `text` via the shared embedding service.

    Returns (vector, model_name). Never raises: when the backend is
    unavailable the row keeps an all-zero vector tagged "none" so consumers
    can distinguish "never embedded" from a real semantic vector.
    """
    if not (text or "").strip():
        return [0.0] * 384, "none"
    try:
        from aja.embeddings.service import get_embedding_service

        service = get_embedding_service()
        vector = service.embed(text)
        return vector, service.get_model_name()
    except Exception as e:  # last resort: write-path must stay non-fatal
        logger.warning(
            "Embedding backend unavailable; storing tagged zero vector "
            "for skill '%s': %s",
            text[:80],
            e,
        )
        return [0.0] * 384, "none"


def _is_unembedded_row(row: dict[str, Any]) -> bool:
    """True when the row's vector is a tagged placeholder (never embedded)."""
    try:
        tags = json.loads(row.get("tags_json") or "[]")
    except (ValueError, TypeError):
        return False
    if not isinstance(tags, list):
        return False
    return any(str(t) == EMBEDDING_MODEL_NONE for t in tags)


class SkillStore:
    """
    High-performance Skill Store (AJA) powered by LanceDB and Apache Arrow.
    Enables semantic skill discovery and SIMD-accelerated retrieval.
    """

    def __init__(self, db_path: Path | str = None):
        self.db_path = (
            Path(db_path) if db_path else DATA_DIR / "lancedb"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))
        self._init_tables()

    def _init_tables(self):
        existing_tables = list_tables_defensive(self.db)

        # 1. AJA Skills Table (Arrow Schema)
        skill_schema = pa.schema(
            [
                ("skill_id", pa.string()),
                ("family_id", pa.string()),
                ("version", pa.int32()),
                ("name", pa.string()),
                ("description", pa.string()),
                ("input_pattern", pa.string()),
                ("tags_json", pa.string()),
                ("tool_sequence_json", pa.string()),
                ("risk_level", pa.string()),
                ("success_count", pa.int32()),
                ("confidence_score", pa.float32()),
                ("created_at", pa.string()),
                ("updated_at", pa.string()),
                (
                    "vector",
                    pa.list_(pa.float32(), 384),
                ),  # For semantic skill discovery
            ]
        )
        if "aja_skills" not in existing_tables:
            self.db.create_table("aja_skills", schema=skill_schema)

        # 2. Skill Sources (Audit Trail)
        source_schema = pa.schema(
            [
                ("skill_id", pa.string()),
                ("task_id", pa.string()),
                ("version", pa.int32()),
                ("captured_at", pa.string()),
            ]
        )
        if "aja_skill_sources" not in existing_tables:
            self.db.create_table("aja_skill_sources", schema=source_schema)

    def save_skill(self, data: Dict[str, Any]) -> str:
        table = self.db.open_table("aja_skills")
        sk_id = data.get("skill_id") or uuid.uuid4().hex
        now = utc_now()

        tags = list(data.get("tags", []))
        text = " ".join(
            part
            for part in (data.get("name", ""), data.get("description", ""))
            if part
        )
        vector, model_name = _embed_or_zero(text)
        tags.append(EMBEDDING_MODEL_TAG_PREFIX + model_name)

        skill_row = [
            {
                "skill_id": sk_id,
                "family_id": data.get("family_id", sk_id),
                "version": data.get("version", 1),
                "name": data.get("name", "Unnamed Skill"),
                "description": data.get("description", ""),
                "input_pattern": data.get("input_pattern", ""),
                "tags_json": json.dumps(tags),
                "tool_sequence_json": json.dumps(data.get("tool_sequence", [])),
                "risk_level": data.get("risk_level", "LOW"),
                "success_count": 1,
                "confidence_score": 1.0,
                "created_at": now,
                "updated_at": now,
                "vector": vector,
            }
        ]
        table.add(skill_row)
        return sk_id

    def update_skill(self, skill_id: str, updates: Dict[str, Any]):
        table = self.db.open_table("aja_skills")
        # Prepare updates for the database schema
        db_updates = updates.copy()
        if "tags" in db_updates:
            db_updates["tags_json"] = json.dumps(db_updates.pop("tags"))
        if "tool_sequence" in db_updates:
            db_updates["tool_sequence_json"] = json.dumps(
                db_updates.pop("tool_sequence")
            )

        db_updates["updated_at"] = utc_now()
        table.update(where=f"skill_id = '{skill_id}'", values=db_updates)

    def delete_skill(self, skill_id: str):
        table = self.db.open_table("aja_skills")
        table.delete(f"skill_id = '{skill_id}'")

    def search_skills(self, query_text: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Semantic search for skills using LanceDB vector indexing.

        Rows that were never embedded (tagged zero-vector placeholders) are
        skipped so cosine similarity is computed only against real vectors.
        """
        table = self.db.open_table("aja_skills")

        from aja.memory.territory import get_text_embedding

        query_vector = get_text_embedding(query_text)
        results = table.search(query_vector).limit(limit).to_list()
        return [row for row in results if not _is_unembedded_row(row)]

    def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_skills")
        results = table.search().where(f"skill_id = '{skill_id}'").limit(1).to_list()
        return results[0] if results else None

    def list_skills(self, limit: int = 50) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_skills")
        return table.to_arrow().slice(0, limit).to_pylist()

def update_skill_metrics(skill_id: str, success: bool) -> None:
    """Atomically fold an execution outcome into the skill's track record."""
    store = SkillStore()
    skill = store.get_skill(skill_id)
    if not skill:
        return
    total = int(skill.get("success_count") or 0) + (1 if success else 0)
    updates = {
        "success_count": total,
        "confidence_score": 1.0 if success else max(0.0, float(skill.get("confidence_score") or 1.0) - 0.05),
        "updated_at": utc_now(),
    }
    store.update_skill(skill_id, updates)


# ---------------------------------------------------------------------------
# Recommendation + normalization (store-row → execute_skill() contract)
# ---------------------------------------------------------------------------

STALE_AFTER_DAYS = 30


def _is_stale_row(row: Dict[str, Any], stale_after_days: int = STALE_AFTER_DAYS) -> bool:
    """True when the row has not been updated within stale_after_days days."""
    updated_at = row.get("updated_at")
    if not updated_at:
        return False
    try:
        ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - ts).days
        return age_days >= stale_after_days
    except (ValueError, TypeError):
        return False


def normalize_skill_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Maps a raw ``aja_skills`` row onto the shape ``execute_skill()`` expects.

    Raw rows expose ``skill_id`` / ``tool_sequence_json`` / ``tags_json``;
    the normalized dict exposes ``id`` / ``tool_sequence`` (decoded list) /
    ``tags`` while preserving the original keys.
    """
    try:
        tool_sequence = json.loads(row.get("tool_sequence_json") or "[]")
        if not isinstance(tool_sequence, list):
            tool_sequence = []
    except (ValueError, TypeError):
        tool_sequence = []
    try:
        tags = json.loads(row.get("tags_json") or "[]")
        if not isinstance(tags, list):
            tags = []
    except (ValueError, TypeError):
        tags = []

    normalized = dict(row)
    normalized["id"] = row.get("skill_id") or row.get("id") or "unknown"
    normalized["tool_sequence"] = tool_sequence
    normalized["tags"] = tags
    return normalized


def recommend_skill(
    query_text: str,
    min_confidence: float = 0.0,
    include_stale: bool = False,
) -> Optional[Dict[str, Any]]:
    """Finds the best matching skill for *query_text* and normalizes it.

    Returns a dict suitable for ``aja.skills.skill_executor.execute_skill``
    (keys: id, tool_sequence, risk_level, ...) or None when no candidate
    passes the confidence/staleness filters.
    """
    try:
        store = SkillStore()
        candidates = store.search_skills(query_text, limit=25)
    except Exception as e:
        logger.warning("recommend_skill() search failed: %s", e)
        return None

    for row in candidates:
        try:
            confidence = float(row.get("confidence_score") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < min_confidence:
            continue
        if not include_stale and _is_stale_row(row):
            continue
        return normalize_skill_row(row)
    return None
