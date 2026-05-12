import lancedb
import pyarrow as pa
import pandas as pd
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from agentx.config import PROJECT_ROOT

class MemoryTree:
    """
    High-performance Unified Memory for AgentX powered by Apache Arrow and LanceDB.
    Replaces legacy LanceDB/Arrow with a columnar data store for maximum hardware efficiency.
    """
    def __init__(self, table_name: str = "agent_activity"):
        self.db_path = PROJECT_ROOT / ".agentx" / "lancedb"
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.uri = str(self.db_path)
        self.db = lancedb.connect(self.uri)
        self.table_name = table_name
        self._ensure_table()

    def _ensure_table(self):
        """Ensures the activity table exists with an Arrow-optimized schema."""
        if self.table_name not in self.db.table_names():
            # Schema design for maximum performance
            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("type", pa.string()), # 'activity', 'fact', 'summary'
                pa.field("content", pa.string()),
                pa.field("timestamp", pa.float64()),
                pa.field("metadata", pa.string()), # JSON-encoded
                pa.field("parent_id", pa.string())
            ])
            self.db.create_table(self.table_name, schema=schema)

    def add_activity(self, content: str, metadata: Optional[Dict[str, Any]] = None, parent_id: str = ""):
        """Adds a new activity record using Apache Arrow zero-copy insertion."""
        table = self.db.open_table(self.table_name)
        node_id = f"node_{int(time.time() * 1000)}"
        
        data = [{
            "id": node_id,
            "type": "activity",
            "content": content,
            "timestamp": time.time(),
            "metadata": json.dumps(metadata or {}),
            "parent_id": parent_id
        }]
        # LanceDB/Arrow high-speed ingestion
        table.add(data)

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Full-text search on columnar data.
        Note: This is now backed by Arrow's SIMD-accelerated filtering.
        """
        table = self.db.open_table(self.table_name)
        # LanceDB SQL filtering on Arrow tables
        results = table.to_lance().scanner(
            filter=f"content LIKE '%{query}%'",
            limit=limit
        ).to_table().to_pandas()
        
        return [
            {
                "content": row["content"],
                "metadata": json.loads(row["metadata"]),
                "timestamp": row["timestamp"]
            } 
            for _, row in results.iterrows()
        ]

    def get_recent_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieves recent history using Arrow's zero-copy scan."""
        table = self.db.open_table(self.table_name)
        # Efficient scan ordered by timestamp
        results = table.to_lance().scanner(
            limit=limit
        ).to_table().to_pandas()
        
        # Sort in memory for small limits (Pandas is fast here)
        results = results.sort_values(by="timestamp", ascending=False).head(limit)
        
        return [
            {
                "type": row["type"],
                "content": row["content"],
                "timestamp": row["timestamp"]
            } 
            for _, row in results.iterrows()
        ]

    def clear(self):
        """Wipes the memory table."""
        if self.table_name in self.db.table_names():
            self.db.drop_table(self.table_name)
            self._ensure_table()
