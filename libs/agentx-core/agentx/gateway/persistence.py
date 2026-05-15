import json
import time
import lancedb
import pyarrow as pa
from typing import Dict, Any, Optional
from agentx.config import PROJECT_ROOT
from agentx.memory.manager import get_memory_manager

class GatewayState:
    """
    Manages persistent state for the AJA Gateway using LanceDB and Apache Arrow.
    Provides zero-copy session handling and high-speed retrieval.
    """

    def __init__(self, table_name: str = "gateway_sessions"):
        self.mgr = get_memory_manager()
        self.db = self.mgr.db
        self.table_name = table_name
        self.init_table()

    def init_table(self):
        """Ensures the gateway session table exists with Arrow schema."""
        schema = pa.schema([
            pa.field("chat_id", pa.string()),
            pa.field("session_json", pa.string()),
            pa.field("last_updated", pa.float64())
        ])
        try:
            self.db.create_table(self.table_name, schema=schema, exist_ok=True)
        except Exception:
            # Fallback if exist_ok is not supported or other issue
            if self.table_name not in self.db.table_names():
                self.db.create_table(self.table_name, schema=schema)

    def get_session(self, chat_id: str) -> Dict[str, Any]:
        """Retrieves session data from LanceDB."""
        table = self.db.open_table(self.table_name)
        # Search by chat_id
        results = table.to_pandas()
        session_row = results[results['chat_id'] == str(chat_id)]
        
        if not session_row.empty:
            return json.loads(session_row.iloc[0]['session_json'])
        
        return {"history": [], "metadata": {}}

    def update_session(self, chat_id: str, update: Dict[str, Any]):
        """Updates session data in LanceDB."""
        session = self.get_session(chat_id)
        session.update(update)
        
        table = self.db.open_table(self.table_name)
        
        # In LanceDB, we usually overwrite or append. 
        # For simplicity in this gateway, we'll delete and re-add or use the update pattern.
        # Since we want performance, we append if not exists, or update the existing row.
        
        data = {
            "chat_id": str(chat_id),
            "session_json": json.dumps(session),
            "last_updated": time.time()
        }
        
        # Delete old entry for this chat_id before adding new one (simulate update)
        table.delete(f'chat_id = "{chat_id}"')
        table.add([data])
