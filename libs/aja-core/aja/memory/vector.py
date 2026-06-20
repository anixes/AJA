import lancedb
import pyarrow as pa
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any, Optional
from aja.memory.manager import list_tables_defensive, get_memory_manager

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
        
        try:
            from aja.embeddings.service import EmbeddingService
            model_name = EmbeddingService().get_model_name()
        except ImportError:
            model_name = "mock-bag-of-words"
            
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
        
        try:
            from aja.embeddings.service import EmbeddingService
            model_name = EmbeddingService().get_model_name()
        except ImportError:
            model_name = "mock-bag-of-words"
            
        processed = []
        for row in results.to_pylist():
            rec_metadata = json.loads(row["metadata"])
            rec_model = rec_metadata.get("embedding_model", "mock-bag-of-words")
            if rec_model != model_name:
                print(f"[VectorMemory] WARNING: Mismatched embedding model '{rec_model}' vs current '{model_name}' on search. Results might be inaccurate.")
                
            processed.append({
                "text": row["text"],
                "metadata": rec_metadata,
                "score": row.get("_distance", 0)
            })
        return processed

    def clear(self):
        """Wipes the memory table."""
        existing = list_tables_defensive(self.db)
            
        if self.table_name in existing:
            self.db.drop_table(self.table_name)
            self.init_table()
