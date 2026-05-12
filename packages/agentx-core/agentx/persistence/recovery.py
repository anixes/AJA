from datetime import datetime, timezone
from agentx.persistence.tasks import fetch_pending_tasks, update_task_status
from agentx.memory.manager import MemoryManager, get_memory_manager

_manager = get_memory_manager()
MAX_RETRIES = 3


def recover_tasks() -> list:
    """
    Recover tasks that were RUNNING (interrupted at shutdown) or PENDING.
    Uses the Unified Arrow Memory stack — no SQLite.
    """
    recovered = []
    try:
        # 1. Find any RUNNING tasks — mark them INTERRUPTED
        table = _manager.get_table("core_tasks")
        running = table.search().where("status = 'RUNNING'").to_list()
        now = datetime.now(timezone.utc).isoformat()

        interrupted_count = 0
        for row in running:
            tid = row["task_id"]
            retry = row.get("retry_count", 0) + 1
            table.update(where=f"task_id = '{tid}'", values={
                "status": "INTERRUPTED",
                "retry_count": retry,
                "updated_at": now
            })
            interrupted_count += 1

        # 2. Re-queue PENDING + INTERRUPTED tasks
        candidates = fetch_pending_tasks(limit=50)
        for task in candidates:
            tid = task["task_id"]
            retry = task.get("retry_count", 0)
            if task["status"] == "INTERRUPTED":
                if retry >= MAX_RETRIES:
                    table.update(where=f"task_id = '{tid}'", values={"status": "FAILED_PERMANENT"})
                    print(f"[Recovery][{tid}] Exceeded retry limit, marking FAILED_PERMANENT.")
                    continue
            print(f"[Recovery][{tid}] Re-queuing {task['status']} task (retry={retry})")
            recovered.append(task)

        print(f"[Recovery] Interrupted {interrupted_count} task(s). Recovered {len(recovered)} for reprocessing.")
    except Exception as e:
        print(f"[Recovery] Failed to recover tasks: {e}")
    return recovered
