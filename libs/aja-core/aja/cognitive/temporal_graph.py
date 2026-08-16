"""
=============================================================================
AJA Cognitive Architecture: Bi-Temporal Entity-Relationship Knowledge Graph
=============================================================================
Implements Bi-Temporal Graph Memory (Zep/Graphiti standard) on SQLite:
- Dual timelines: Real-World Validity (valid_from/valid_to) vs System Ingestion (recorded_at)
- Non-destructive contradiction resolution: facts are superseded by cascade invalidation
- Point-in-time historical queries + full-text search (FTS5) + episode provenance
=============================================================================
"""

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TemporalEntity:
    entity_id: str
    entity_type: str
    name: str
    properties: Dict[str, Any]
    valid_from: float
    valid_to: Optional[float] = None  # None indicates currently active
    recorded_at: float = field(default_factory=time.time)
    source_episode_id: Optional[str] = None

    @property
    def is_active(self) -> bool:
        now = time.time()
        return self.valid_from <= now and (self.valid_to is None or self.valid_to > now)


@dataclass
class TemporalRelation:
    relation_id: str
    source_id: str
    target_id: str
    relation_type: str
    properties: Dict[str, Any]
    valid_from: float
    valid_to: Optional[float] = None
    recorded_at: float = field(default_factory=time.time)


class BiTemporalEntityGraph:
    """
    Bi-temporal graph database for persistent, contradiction-free agent memory.
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_dir = Path.home() / ".aja" / "state"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "temporal_graph.db"
        else:
            db_path = Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                valid_from REAL NOT NULL,
                valid_to REAL,
                recorded_at REAL NOT NULL,
                source_episode_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_entities_name_type ON entities(name, entity_type);
            CREATE INDEX IF NOT EXISTS idx_entities_valid ON entities(valid_from, valid_to);

            CREATE TABLE IF NOT EXISTS relations (
                relation_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                valid_from REAL NOT NULL,
                valid_to REAL,
                recorded_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
            CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);

            -- Full-text search index for entity names and property text
            CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                entity_id UNINDEXED,
                entity_type,
                name,
                properties_text
            );
            """)

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        properties: Dict[str, Any],
        source_episode_id: Optional[str] = None,
        valid_from: Optional[float] = None,
    ) -> TemporalEntity:
        """
        Upserts an entity bi-temporally.
        If an active entity with the same (entity_type, name) exists with changed properties,
        the old entity is invalidated (valid_to = now), preserving history, and a new record is created.
        """
        now = time.time()
        vf = valid_from if valid_from is not None else now

        with self._get_connection() as conn:
            # Check for existing active entity
            cursor = conn.execute(
                """
                SELECT entity_id, properties_json FROM entities
                WHERE entity_type = ? AND name = ? AND (valid_to IS NULL OR valid_to > ?)
                ORDER BY valid_from DESC LIMIT 1
                """,
                (entity_type, name, now),
            )
            row = cursor.fetchone()

            if row:
                existing_id = row["entity_id"]
                existing_props = json.loads(row["properties_json"])

                # If properties are unchanged, return existing active entity
                if existing_props == properties:
                    return TemporalEntity(
                        entity_id=existing_id,
                        entity_type=entity_type,
                        name=name,
                        properties=properties,
                        valid_from=vf,
                        valid_to=None,
                        source_episode_id=source_episode_id,
                    )

                # Invalidate existing entity as superseded
                conn.execute(
                    "UPDATE entities SET valid_to = ? WHERE entity_id = ?",
                    (vf, existing_id),
                )

            # Insert new active entity
            new_id = f"ent-{uuid.uuid4().hex[:12]}"
            props_json = json.dumps(properties)
            props_text = " ".join(f"{k} {v}" for k, v in properties.items())

            conn.execute(
                """
                INSERT INTO entities (entity_id, entity_type, name, properties_json, valid_from, valid_to, recorded_at, source_episode_id)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (new_id, entity_type, name, props_json, vf, now, source_episode_id),
            )

            conn.execute(
                """
                INSERT INTO entities_fts (entity_id, entity_type, name, properties_text)
                VALUES (?, ?, ?, ?)
                """,
                (new_id, entity_type, name, props_text),
            )

            return TemporalEntity(
                entity_id=new_id,
                entity_type=entity_type,
                name=name,
                properties=properties,
                valid_from=vf,
                valid_to=None,
                recorded_at=now,
                source_episode_id=source_episode_id,
            )

    def invalidate_entity(self, entity_type: str, name: str, invalid_at: Optional[float] = None) -> bool:
        """Explicitly marks an active entity as invalid/inactive."""
        now = invalid_at or time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE entities SET valid_to = ?
                WHERE entity_type = ? AND name = ? AND (valid_to IS NULL OR valid_to > ?)
                """,
                (now, entity_type, name, now),
            )
            return cursor.rowcount > 0

    def get_active_entity(self, entity_type: str, name: str) -> Optional[TemporalEntity]:
        """Fetches the currently valid entity by type and name."""
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM entities
                WHERE entity_type = ? AND name = ? AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)
                ORDER BY valid_from DESC LIMIT 1
                """,
                (entity_type, name, now, now),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_entity(row)

    def get_entity_history(self, entity_type: str, name: str) -> List[TemporalEntity]:
        """Returns the full chronological history of an entity across all revisions."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM entities
                WHERE entity_type = ? AND name = ?
                ORDER BY valid_from ASC
                """,
                (entity_type, name),
            )
            return [self._row_to_entity(row) for row in cursor.fetchall()]

    def search_entities(self, query: str, limit: int = 10, only_active: bool = True) -> List[TemporalEntity]:
        """Full-text keyword search across active or historical entities."""
        now = time.time()
        clean_q = query.replace('"', '""').replace("'", "''").strip()
        if not clean_q:
            return []

        with self._get_connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    SELECT e.* FROM entities_fts f
                    JOIN entities e ON f.entity_id = e.entity_id
                    WHERE entities_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (clean_q, limit * 2),
                )
                entities = [self._row_to_entity(row) for row in cursor.fetchall()]
                if only_active:
                    entities = [e for e in entities if e.is_active]
                return entities[:limit]
            except sqlite3.OperationalError:
                # Fallback to simple LIKE query if FTS query syntax fails
                cursor = conn.execute(
                    """
                    SELECT * FROM entities
                    WHERE name LIKE ? OR properties_json LIKE ?
                    LIMIT ?
                    """,
                    (f"%{clean_q}%", f"%{clean_q}%", limit),
                )
                entities = [self._row_to_entity(row) for row in cursor.fetchall()]
                if only_active:
                    entities = [e for e in entities if e.is_active]
                return entities[:limit]

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        properties: Optional[Dict[str, Any]] = None,
        valid_from: Optional[float] = None,
    ) -> TemporalRelation:
        """Links two entities with a directed bi-temporal relationship."""
        now = time.time()
        vf = valid_from or now
        props = properties or {}
        rel_id = f"rel-{uuid.uuid4().hex[:12]}"

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO relations (relation_id, source_id, target_id, relation_type, properties_json, valid_from, valid_to, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (rel_id, source_id, target_id, relation_type, json.dumps(props), vf, now),
            )

        return TemporalRelation(
            relation_id=rel_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            properties=props,
            valid_from=vf,
            valid_to=None,
            recorded_at=now,
        )

    def get_relations(self, entity_id: str, only_active: bool = True) -> List[TemporalRelation]:
        """Returns all outbound and inbound relationships for an entity."""
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM relations
                WHERE (source_id = ? OR target_id = ?)
                ORDER BY valid_from DESC
                """,
                (entity_id, entity_id),
            )
            relations = [
                TemporalRelation(
                    relation_id=row["relation_id"],
                    source_id=row["source_id"],
                    target_id=row["target_id"],
                    relation_type=row["relation_type"],
                    properties=json.loads(row["properties_json"]),
                    valid_from=row["valid_from"],
                    valid_to=row["valid_to"],
                    recorded_at=row["recorded_at"],
                )
                for row in cursor.fetchall()
            ]

            if only_active:
                relations = [r for r in relations if r.valid_from <= now and (r.valid_to is None or r.valid_to > now)]
            return relations

    def get_context_summary(self, limit: int = 15) -> str:
        """Generates structured markdown summary of active environment entities."""
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM entities
                WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)
                ORDER BY entity_type ASC, name ASC LIMIT ?
                """,
                (now, now, limit),
            )
            entities = [self._row_to_entity(row) for row in cursor.fetchall()]

        if not entities:
            return ""

        lines = ["### Active Environment Knowledge Graph (Bi-Temporal State):"]
        for e in entities:
            props_str = ", ".join(f"{k}: `{v}`" for k, v in list(e.properties.items())[:3])
            lines.append(f"- **[{e.entity_type.upper()}] {e.name}**: {props_str}")
        return "\n".join(lines)

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> TemporalEntity:
        return TemporalEntity(
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            name=row["name"],
            properties=json.loads(row["properties_json"]),
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            recorded_at=row["recorded_at"],
            source_episode_id=row["source_episode_id"],
        )
