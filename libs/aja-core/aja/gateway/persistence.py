import json
import time
import lancedb
import pyarrow as pa
from typing import Dict, Any, Optional
from aja.config import PROJECT_ROOT
from aja.memory.manager import get_memory_manager, list_tables_defensive


def _sql_quote(value: Any) -> str:
    """Escapes a value for safe interpolation into a LanceDB SQL predicate.

    Matches secretary.sanitize_value semantics: single-quoted SQL literal with
    embedded single quotes doubled. Double quotes need no escaping here.
    """
    safe = str(value).replace("'", "''")
    return f"'{safe}'"


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
            if self.table_name not in list_tables_defensive(self.db):
                self.db.create_table(self.table_name, schema=schema)

    def get_session(self, chat_id: str) -> Dict[str, Any]:
        """Retrieves session data from LanceDB (targeted lookup, not a full scan)."""
        table = self.db.open_table(self.table_name)
        results = (
            table.search()
            .where(f"chat_id = {_sql_quote(chat_id)}")
            .limit(1)
            .to_list()
        )
        if results:
            raw = results[0].get("session_json")
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                pass
        return {"history": [], "metadata": {}}

    def update_session(self, chat_id: str, update: Dict[str, Any]):
        """Atomically upserts session data in LanceDB keyed on chat_id.

        merge_insert performs the existence check + insert/update server-side,
        so concurrent updates can no longer lose one side's writes via a
        read-modify-write race or leave duplicate rows behind on delete failure.
        """
        session = self.get_session(chat_id)
        session.update(update)

        table = self.db.open_table(self.table_name)
        data = {
            "chat_id": str(chat_id),
            "session_json": json.dumps(session),
            "last_updated": time.time(),
        }
        (
            table.merge_insert(on="chat_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute([data])
        )
