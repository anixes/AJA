import lancedb
import pyarrow as pa
from pathlib import Path
from agent.config import PROJECT_ROOT

def list_tables_defensive(db) -> list[str]:
    """Returns a list of table names, handling LanceDB's TableList object defensively."""
    tables = db.list_tables()
    if hasattr(tables, "tables"):
        return tables.tables
    return tables

class MemoryManager:
    """
    Unified Memory Manager for Pure Agent.
    Consolidates all core engine tables into a high-performance LanceDB/Arrow stack.
    """
    def __init__(self, db_path: Path | str = None):
        self.db_path = Path(db_path) if db_path else PROJECT_ROOT / ".agent" / "lancedb"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))
        self._init_core_tables()

    def _init_core_tables(self):
        existing = list_tables_defensive(self.db)

        # 1. Tasks Table (Core)
        task_schema = pa.schema([
            ("task_id", pa.string()),
            ("run_id", pa.string()),
            ("node_id", pa.string()),  # Link to PlanIR Node ID
            ("objective", pa.string()),
            ("status", pa.string()),
            ("retry_count", pa.int32()),
            ("created_at", pa.string()),
            ("updated_at", pa.string()),
            ("metadata_json", pa.string())
        ])
        if "core_tasks" not in existing:
            self.db.create_table("core_tasks", schema=task_schema)

        # 2. Tool Executions (The performance bottleneck)
        tool_schema = pa.schema([
            ("execution_id", pa.string()),
            ("task_id", pa.string()),
            ("tool_name", pa.string()),
            ("args_json", pa.string()),
            ("status", pa.string()),
            ("output_summary", pa.string()),
            ("created_at", pa.string())
        ])
        if "core_tool_executions" not in existing:
            self.db.create_table("core_tool_executions", schema=tool_schema)

        # 3. Plan Store (Semantic)
        plan_schema = pa.schema([
            ("plan_id", pa.string()),
            ("goal", pa.string()),
            ("steps_json", pa.string()),
            ("status", pa.string()),
            ("created_at", pa.string()),
            ("vector", pa.list_(pa.float32(), 1536))
        ])
        if "core_plans" not in existing:
            self.db.create_table("core_plans", schema=plan_schema)

        # 4. Presence Triggers
        trigger_schema = pa.schema([
            ("trigger_id", pa.string()),
            ("event_pattern", pa.string()),
            ("action_json", pa.string()),
            ("status", pa.string()),
            ("created_at", pa.string())
        ])
        if "core_triggers" not in existing:
            self.db.create_table("core_triggers", schema=trigger_schema)

        # 5. Core Events (Replaces assistant_runtime_events)
        event_schema = pa.schema([
            ("event_id", pa.string()),
            ("event_type", pa.string()),
            ("message", pa.string()),
            ("level", pa.string()),
            ("created_at", pa.string())
        ])
        if "core_events" not in existing:
            self.db.create_table("core_events", schema=event_schema)

        # 6. Core Approvals (Replaces assistant_approvals)
        approval_schema = pa.schema([
            ("approval_id", pa.string()),
            ("run_id", pa.string()),
            ("command", pa.string()),
            ("risk_level", pa.string()),
            ("status", pa.string()),
            ("resolution_note", pa.string()),
            ("created_at", pa.string()),
            ("updated_at", pa.string()),
            ("metadata_json", pa.string())
        ])
        if "core_approvals" not in existing:
            self.db.create_table("core_approvals", schema=approval_schema)

        # 7. Core Approval Audit (Append-only trail)
        audit_schema = pa.schema([
            ("approval_id", pa.string()),
            ("action", pa.string()),
            ("requester_source", pa.string()),
            ("command", pa.string()),
            ("risk_level", pa.string()),
            ("reasons_json", pa.string()),
            ("exit_code", pa.int32()),
            ("note", pa.string()),
            ("created_at", pa.string())
        ])
        if "core_approval_audit" not in existing:
            self.db.create_table("core_approval_audit", schema=audit_schema)

    def get_table(self, name: str):
        return self.db.open_table(name)


# ── Singleton Factory ─────────────────────────────────────────────────────────
# All modules must call get_memory_manager() instead of MemoryManager() directly.
# This ensures exactly one LanceDB connection is opened per process, preventing
# file-lock contention on cheap hardware (PERF-01).
_instance: "MemoryManager | None" = None

def get_memory_manager() -> MemoryManager:
    """Returns the process-wide MemoryManager singleton."""
    global _instance
    if _instance is None:
        _instance = MemoryManager()
    return _instance
