import lancedb
import pyarrow as pa
import pandas as pd
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from aja.memory.manager import list_tables_defensive, get_memory_manager
from aja.embeddings.service import get_embedding_service

logger = logging.getLogger(__name__)

# Tables already warned about for embedding-model mismatches (once per process).
_WARNED_TABLES: set[str] = set()

class VectorMemory:
    """
    High-performance Semantic Memory powered by LanceDB and Apache Arrow.
    Provides O(1) retrieval and zero-copy data handling to keep hardware costs low.
    """
    def __init__(self, table_name: str = "agent_memory"):
        mgr = get_memory_manager()
        self.db = mgr.db
        self.table_name = table_name
        self.init_table()

    def init_table(self):
        """Ensures the memory table exists with the correct schema."""
        existing = list_tables_defensive(self.db)
            
        if self.table_name not in existing:
            # Define schema using Arrow
            schema = pa.schema([
                pa.field("vector", pa.list_(pa.float32(), 384)), # Standardized for local models
                pa.field("text", pa.string()),
                pa.field("metadata", pa.string()), # JSON-encoded metadata
                pa.field("timestamp", pa.float64())
            ])
            self.db.create_table(self.table_name, schema=schema)

    def add(self, text: str, vector: List[float], metadata: Dict[str, Any] = None):
        """Adds a new semantic record to the memory."""
        import time
        import json
        
        model_name = get_embedding_service().get_model_name()

        full_metadata = metadata or {}
        full_metadata["embedding_model"] = model_name
        
        table = self.db.open_table(self.table_name)
        data = [{
            "vector": vector,
            "text": text,
            "metadata": json.dumps(full_metadata),
            "timestamp": time.time()
        }]
        # LanceDB uses Arrow internally for high-speed insertion
        table.add(data)

    def search(self, query_vector: List[float], limit: int = 5, filter_str: Optional[str] = None) -> List[Dict[str, Any]]:
        """Performs a semantic search using vector similarity and optional metadata pre-filtering."""
        import json
        table = self.db.open_table(self.table_name)
        
        query = table.search(query_vector)
        if filter_str:
            query = query.where(filter_str)
            
        results = query.limit(limit).to_arrow()
        
        model_name = get_embedding_service().get_model_name()

        mismatched_models: set[str] = set()
        processed = []
        for row in results.to_pylist():
            rec_metadata = json.loads(row["metadata"])
            rec_model = rec_metadata.get("embedding_model")
            # Rows indexed under a different embedding model live in a
            # different vector space — their distances are meaningless, so
            # they are filtered out (reindex with 'aja reindex-embeddings').
            if rec_model and rec_model != model_name:
                mismatched_models.add(rec_model)
                continue
            if not rec_model:
                # Legacy rows written before model stamping existed.
                rec_metadata["embedding_model"] = "unknown"

            processed.append({
                "text": row["text"],
                "metadata": rec_metadata,
                "score": row.get("_distance", 0)
            })

        if mismatched_models and self.table_name not in _WARNED_TABLES:
            logger.warning(
                "[VectorMemory] Embedding model mismatch on table '%s': rows "
                "indexed with [%s] vs current '%s'. Results might be inaccurate.",
                self.table_name,
                ", ".join(sorted(mismatched_models)),
                model_name,
            )
            _WARNED_TABLES.add(self.table_name)
        return processed

    def clear(self):
        """Wipes the memory table."""
        existing = list_tables_defensive(self.db)
            
        if self.table_name in existing:
            self.db.drop_table(self.table_name)
            self.init_table()
