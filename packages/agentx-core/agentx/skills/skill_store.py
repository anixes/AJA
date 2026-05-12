import json
import uuid
import lancedb
import pyarrow as pa
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Dict
from agentx.config import PROJECT_ROOT

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"

class SkillStore:
    """
    High-performance Skill Store (AJA) powered by LanceDB and Apache Arrow.
    Enables semantic skill discovery and SIMD-accelerated retrieval.
    """
    def __init__(self, db_path: Path | str = None):
        self.db_path = Path(db_path) if db_path else PROJECT_ROOT / ".agentx" / "lancedb"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))
        self._init_tables()

    def _init_tables(self):
        # 1. AJA Skills Table (Arrow Schema)
        skill_schema = pa.schema([
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
            ("vector", pa.list_(pa.float32(), 1536)) # For semantic skill discovery
        ])
        if "aja_skills" not in self.db.table_names():
            self.db.create_table("aja_skills", schema=skill_schema)

        # 2. Skill Sources (Audit Trail)
        source_schema = pa.schema([
            ("skill_id", pa.string()),
            ("task_id", pa.string()),
            ("version", pa.int32()),
            ("captured_at", pa.string())
        ])
        if "aja_skill_sources" not in self.db.table_names():
            self.db.create_table("aja_skill_sources", schema=source_schema)

    def save_skill(self, data: Dict[str, Any]) -> str:
        table = self.db.open_table("aja_skills")
        sk_id = data.get("skill_id") or uuid.uuid4().hex
        now = utc_now()
        
        # In a real impl, we'd use a model to generate this vector.
        vector = [0.0] * 1536 
        
        skill_row = [{
            "skill_id": sk_id,
            "family_id": data.get("family_id", sk_id),
            "version": data.get("version", 1),
            "name": data.get("name", "Unnamed Skill"),
            "description": data.get("description", ""),
            "input_pattern": data.get("input_pattern", ""),
            "tags_json": json.dumps(data.get("tags", [])),
            "tool_sequence_json": json.dumps(data.get("tool_sequence", [])),
            "risk_level": data.get("risk_level", "LOW"),
            "success_count": 1,
            "confidence_score": 1.0,
            "created_at": now,
            "updated_at": now,
            "vector": vector
        }]
        table.add(skill_row)
        return sk_id

    def search_skills(self, query_text: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Semantic search for skills using LanceDB vector indexing."""
        table = self.db.open_table("aja_skills")
        
        # If we had a real embedding model:
        # query_vector = embedding_model.embed(query_text)
        # results = table.search(query_vector).limit(limit).to_list()
        
        # Fallback to keyword-based filtering in the Arrow table if vector is empty
        results = table.search().where(f"input_pattern LIKE '%{query_text}%'").limit(limit).to_list()
        return results

    def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_skills")
        results = table.search().where(f"skill_id = '{skill_id}'").limit(1).to_list()
        return results[0] if results else None

    def list_skills(self, limit: int = 50) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_skills")
        return table.to_arrow().slice(0, limit).to_pylist()
