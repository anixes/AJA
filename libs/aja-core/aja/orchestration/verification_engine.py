from __future__ import annotations

from pathlib import Path


def run_verification(baton: dict, workspace_dir: str) -> dict:
    """
    Lightweight independent verification for baton worker output.

    This does not prove semantic correctness, but it prevents the worker from
    accepting empty or internally inconsistent results as completed work.
    """
    checks: list[dict] = []

    output = str(baton.get("output") or "").strip()
    diff = str(baton.get("diff") or "").strip()
    tests_output = str(baton.get("tests_output") or "").strip()
    status = baton.get("status")
    worker_id = baton.get("delegated_worker", "swarm-maintenance")
    workspace = Path(workspace_dir)

    checks.append(
        {
            "name": "workspace_exists",
            "passed": workspace.exists() and workspace.is_dir(),
            "message": f"Workspace: {workspace}",
        }
    )
    checks.append(
        {
            "name": "worker_returned_output",
            "passed": bool(output or diff or tests_output),
            "message": "Worker produced output, diff, or test logs.",
        }
    )

    if status == "failed":
        checks.append(
            {
                "name": "adapter_status",
                "passed": False,
                "message": baton.get("error") or "Adapter reported failure.",
            }
        )

    if tests_output:
        failed = "tests failed:" in tests_output.lower()
        checks.append(
            {
                "name": "tests_passed",
                "passed": not failed,
                "message": "Test output did not report failure." if not failed else "Test output reports failure.",
            }
        )

    is_maintenance = (worker_id == "swarm-maintenance" or baton.get("rollback_path") == "No rollback needed for maintenance tasks.")
    if not is_maintenance and not diff and not output and not tests_output:
        checks.append(
            {
                "name": "missing_output",
                "passed": False,
                "message": f"Worker {worker_id} completed without producing any output, diff, or test results.",
            }
        )

    passed = all(check["passed"] for check in checks)
    return {
        "passed": passed,
        "checks": checks,
    }
