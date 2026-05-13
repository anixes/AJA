import subprocess
import os
import re

def run_verification(baton: dict, workspace_dir: str) -> dict:
    """
    Independently verify the worker's execution quality.
    """
    checks = []
    
    # 1. Tests executed successfully
    tests_output = baton.get("tests_output", "")
    tests_passed = "failed" not in tests_output.lower() and "error" not in tests_output.lower()
    if not tests_output.strip():
        tests_passed = False
        checks.append({"name": "Test Execution", "status": "fail", "message": "No test output found. Worker must run tests."})
    else:
        status = "pass" if tests_passed else "fail"
        checks.append({"name": "Test Execution", "status": status, "message": "Tests executed successfully." if tests_passed else "Tests failed or encountered errors."})

    # 1.5 Branch exists
    branch_check = subprocess.run(["git", "branch", "--show-current"], cwd=workspace_dir, capture_output=True, text=True)
    branch_name = branch_check.stdout.strip()
    branch_passed = bool(branch_name and branch_name != "master" and branch_name != "main")
    checks.append({"name": "Branch Isolation", "status": "pass" if branch_passed else "fail", "message": f"Worker isolated work on branch: {branch_name}" if branch_passed else "Worker did not isolate work on a separate branch."})

    # 2. Diff exists
    diff_output = baton.get("diff", "")
    diff_passed = bool(diff_output.strip())
    checks.append({"name": "Diff Exists", "status": "pass" if diff_passed else "fail", "message": "Files were modified." if diff_passed else "No files were changed."})

    # 3. Rollback path exists
    rollback = baton.get("rollback_path", "")
    rollback_passed = bool(rollback.strip())
    checks.append({"name": "Rollback Path", "status": "pass" if rollback_passed else "fail", "message": "Rollback command provided." if rollback_passed else "No rollback command provided."})

    # 4. No obvious secret leakage
    secret_patterns = [r"sk-[a-zA-Z0-9]{40,}", r"AKIA[0-9A-Z]{16}", r"ghp_[a-zA-Z0-9]{36}"]
    leak_passed = True
    leak_message = "No obvious secrets detected in diff."
    for pattern in secret_patterns:
        if re.search(pattern, diff_output):
            leak_passed = False
            leak_message = "Potential secret leakage detected in diff."
            break
    checks.append({"name": "Secret Leakage Check", "status": "pass" if leak_passed else "fail", "message": leak_message})
    
    # 5. Definition of Done checklist satisfied
    dod = baton.get("definition_of_done", [])
    dod_passed = bool(dod)
    checks.append({"name": "DoD Present", "status": "pass" if dod_passed else "fail", "message": "Definition of Done criteria is tracked." if dod_passed else "Missing Definition of Done."})

    all_passed = all(c["status"] == "pass" for c in checks)
    
    return {
        "passed": all_passed,
        "checks": checks
    }
