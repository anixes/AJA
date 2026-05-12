import lancedb
import pyarrow as pa
from pathlib import Path
from agentx.config import PROJECT_ROOT

class MemoryManager:
    """
    Unified Memory Manager for Pure AgentX.
    Consolidates all core engine tables into a high-performance LanceDB/Arrow stack.
    """
    def __init__(self, db_path: Path | str = None):
        self.db_path = Path(db_path) if db_path else PROJECT_ROOT / ".agentx" / "lancedb"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))
        self._init_core_tables()

    def _init_core_tables(self):
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
        if "core_tasks" not in self.db.table_names():
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
        if "core_tool_executions" not in self.db.table_names():
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
        if "core_plans" not in self.db.table_names():
            self.db.create_table("core_plans", schema=plan_schema)

        # 4. Presence Triggers
        trigger_schema = pa.schema([
            ("trigger_id", pa.string()),
            ("event_pattern", pa.string()),
            ("action_json", pa.string()),
            ("status", pa.string()),
            ("created_at", pa.string())
        ])
        if "core_triggers" not in self.db.table_names():
            self.db.create_table("core_triggers", schema=trigger_schema)

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
