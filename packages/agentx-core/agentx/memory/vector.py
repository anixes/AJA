import lancedb
import pyarrow as pa
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any, Optional
from agentx.config import PROJECT_ROOT

class VectorMemory:
    """
    High-performance Semantic Memory powered by LanceDB and Apache Arrow.
    Provides O(1) retrieval and zero-copy data handling to keep hardware costs low.
    """
    def __init__(self, table_name: str = "agent_memory"):
        self.db_path = PROJECT_ROOT / ".agentx" / "lancedb"
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.uri = str(self.db_path)
        self.db = lancedb.connect(self.uri)
        self.table_name = table_name
        self._ensure_table()

    def _ensure_table(self):
        """Ensures the memory table exists with the correct schema."""
        if self.table_name not in self.db.table_names():
            # Define schema using Arrow
            schema = pa.schema([
                pa.field("vector", pa.list_(pa.float32(), 1536)), # Default for many models
                pa.field("text", pa.string()),
                pa.field("metadata", pa.string()), # JSON-encoded metadata
                pa.field("timestamp", pa.float64())
            ])
            self.db.create_table(self.table_name, schema=schema)

    def add(self, text: str, vector: List[float], metadata: Dict[str, Any] = None):
        """Adds a new semantic record to the memory."""
        import time
        import json
        
        table = self.db.open_table(self.table_name)
        data = [{
            "vector": vector,
            "text": text,
            "metadata": json.dumps(metadata or {}),
            "timestamp": time.time()
        }]
        # LanceDB uses Arrow internally for high-speed insertion
        table.add(data)

    def search(self, query_vector: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        """Performs a semantic search using vector similarity."""
        import json
        table = self.db.open_table(self.table_name)
        results = table.search(query_vector).limit(limit).to_pandas()
        
        processed = []
        for _, row in results.iterrows():
            processed.append({
                "text": row["text"],
                "metadata": json.loads(row["metadata"]),
                "score": row.get("_distance", 0)
            })
        return processed

    def clear(self):
        """Wipes the memory table."""
        if self.table_name in self.db.table_names():
            self.db.drop_table(self.table_name)
            self._ensure_table()
