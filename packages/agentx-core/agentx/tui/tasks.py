import time
import uuid
import re
import pyarrow as pa
import pyarrow.compute as pc
from typing import Optional
from agentx.memory.manager import get_memory_manager

# AgentX Canonical Statuses
STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_FAILED = "FAILED"
STATUS_COMPLETED = "COMPLETED"

# Regex to validate task IDs (12 hex chars)
_TASK_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _validate_task_id(task_id: str) -> str:
    """Guards against injection via malformed task_id."""
    if not _TASK_ID_RE.match(task_id):
        raise ValueError(f"Invalid task_id format: '{task_id}'. Expected 12 hex chars.")
    return task_id


class TaskManager:
    def __init__(self):
        self.manager = get_memory_manager()  # singleton — no extra LanceDB connection
        self.table_name = "core_tasks"

    def add_task(self, title: str, node_id: str = "") -> str:
        """Adds a new mission objective to the core_tasks table."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        task_id = str(uuid.uuid4()).replace("-", "")[
            :12
        ]  # 12 chars — lower collision risk

        data = [
            {
                "task_id": task_id,
                "run_id": "",
                "node_id": node_id,
                "objective": title,
                "status": STATUS_PENDING,
                "retry_count": 0,
                "created_at": now,
                "updated_at": now,
                "metadata_json": "{}",
            }
        ]

        table = self.manager.get_table(self.table_name)
        table.add(data)
        return task_id

    def get_tasks_by_status(self, status: str) -> pa.Table:
        """Returns tasks as an Arrow Table for zero-copy efficiency."""
        table = self.manager.get_table(self.table_name)
        return table.search().where(f"status = '{status}'").to_arrow()

    def update_status(self, task_id: str, status: str):
        """Transactional state update in LanceDB. Validates task_id first."""
        _validate_task_id(task_id)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        table = self.manager.get_table(self.table_name)
        table.update(
            where=f"task_id = '{task_id}'", values={"status": status, "updated_at": now}
        )

    def delete_task(self, task_id: str):
        """Removes task from the columnar store. Validates task_id first."""
        _validate_task_id(task_id)
        table = self.manager.get_table(self.table_name)
        table.delete(f"task_id = '{task_id}'")

    def get_counts(self) -> tuple[int, int]:
        """
        Returns (pending, running) counts using Arrow compute — no Python-level iteration.
        Fixes BUG-01: was using pa.compute.equal (NameError); now uses pc.sum(pc.equal(...)).
        """
        arrow_table = self.manager.get_table(self.table_name).to_arrow()
        if len(arrow_table) == 0:
            return 0, 0
        status_col = arrow_table["status"]
        pending = pc.sum(pc.equal(status_col, STATUS_PENDING)).as_py() or 0
        running = pc.sum(pc.equal(status_col, STATUS_RUNNING)).as_py() or 0
        return pending, running
