import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from agentx.orchestration.gateway import UnifiedGateway


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_baton_history(baton, stage, message):
    baton.setdefault("history", []).append(
        {
            "stage": stage,
            "message": message,
            "timestamp": now_iso(),
        }
    )
    baton["updated_at"] = now_iso()


def save_baton(path: Path, baton):
    path.write_text(json.dumps(baton, indent=2))


async def work(baton_path: str):
    """
    The worker entry point.
    Reads a baton file, executes the assigned task, and updates the baton.
    """
    path = Path(baton_path)
    if not path.exists():
        print(f"Error: Baton file {baton_path} not found.")
        return

    baton = json.loads(path.read_text())
    print(f"Worker {baton['id']} started. Task: {baton['task']}")

    # Use the central LLM router which respects operating_mode
    from agentx.llm import get_gateway_for_model
    model_str = os.getenv("AI_MODEL", "gemma-4-e2b")
    gateway, model_name = get_gateway_for_model(model_str)

    prompt = (
        f"Context: {baton['context']}\n"
        f"Task: {baton['task']}\n"
        "Execute this task and provide a detailed report of the work completed."
    )

    from scripts.dispatch_adapters import dispatch_worker

    try:
        baton["status"] = "executing"
        baton["stage"] = "working"
        append_baton_history(baton, "working", f"Worker {baton.get('delegated_worker', 'unknown')} started execution.")
        save_baton(path, baton)

        workspace_dir = str(Path(__file__).resolve().parent.parent)
        worker_id = baton.get("delegated_worker", "swarm-maintenance")
        
        # Execute via specialized dispatch adapter
        adapter_result = dispatch_worker(worker_id, baton, workspace_dir)
        
        baton["stage"] = "verifying"
        append_baton_history(baton, "verifying", "Worker completed execution. Gathering validation and diffs.")
        save_baton(path, baton)

        if adapter_result.get("status") == "completed":
            baton["output"] = adapter_result.get("output", "")
            baton["diff"] = adapter_result.get("diff", "")
            baton["tests_output"] = adapter_result.get("tests", "")
            baton["rollback_path"] = adapter_result.get("rollback_path", "")
            
            from scripts.verification_engine import run_verification
            verif = run_verification(baton, workspace_dir)
            baton["verification"] = verif
            
            if not verif["passed"]:
                baton["status"] = "verification_failed"
                baton["stage"] = "verification_failed"
                append_baton_history(baton, "verification_failed", "Worker completed but independent verification failed.")
            else:
                baton["status"] = "completed"
                baton["stage"] = "worker_complete"
                append_baton_history(baton, "worker_complete", "Worker finished and passed verification.")
        else:
            baton["status"] = "failed"
            baton["stage"] = "worker_failed"
            baton["error"] = adapter_result.get("error", "Unknown error in adapter")
            baton["output"] = adapter_result.get("output", "")
            baton["rollback_path"] = adapter_result.get("rollback_path", "")
            append_baton_history(baton, "worker_failed", f"Worker failed: {baton['error']}")

        save_baton(path, baton)
        print(f"DONE: Worker {baton['id']} finished with status: {baton['status']}.")

    except Exception as e:
        baton["status"] = "failed"
        baton["stage"] = "worker_failed"
        baton["error"] = str(e)
        append_baton_history(baton, "worker_failed", f"Worker raised an exception: {e}")
        save_baton(path, baton)
        print(f"ERROR: Worker {baton['id']} failed: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent_worker.py <baton_path>")
        sys.exit(1)

    asyncio.run(work(sys.argv[1]))
