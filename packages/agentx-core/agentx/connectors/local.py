import os
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from .base import BaseConnector, Document, ConnectorRegistry

@ConnectorRegistry.register("local_docs")
class LocalDocsConnector(BaseConnector):
    """
    A local connector that indexes documents from a specified directory.
    """
    connector_id = "local_docs"
    display_name = "Local Document Indexer"
    auth_type = "local"

    def __init__(self, root_dir: str = "."):
        self.root_dir = Path(root_dir)

    def is_connected(self) -> bool:
        return self.root_dir.exists()

    def sync(self, since: Optional[datetime] = None) -> List[Document]:
        documents = []
        # Support common doc types
        extensions = {".txt", ".md", ".py", ".js", ".json"}
        
        for file_path in self.root_dir.rglob("*"):
            if file_path.suffix in extensions:
                try:
                    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if since and mtime < since:
                        continue
                    
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        
                    documents.append(Document(
                        doc_id=f"local:{file_path}",
                        source="local_docs",
                        content=content,
                        title=file_path.name,
                        timestamp=mtime,
                        metadata={"path": str(file_path), "size": file_path.stat().st_size}
                    ))
                except Exception as e:
                    continue
        return documents

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "search_local_docs",
                "description": "Search for text within locally indexed documents.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5}
                    },
                    "required": ["query"]
                }
            }
        ]
